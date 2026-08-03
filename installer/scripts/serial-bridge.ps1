# serial-bridge.ps1 -- COM port -> TCP mirror service (EnvisoftWebX-SerialBridge)
#
# Containers inside Docker/WSL2 cannot open Windows COM ports. This service
# mirrors each serial Connection (rendered by the web app into
# bridge\serial-bridge.json) to a TCP listener; the SCADA worker connects via
# the existing Modbus-RTU-over-TCP path (see scada_io/bridge_redirect.py).
#
# Design:
#   - Config file polled every 5 s by mtime; malformed JSON keeps last state.
#   - Up to 8 concurrent TCP clients per bridge (prefork workers + web tests);
#     traffic is serialized onto the serial port per request/response
#     transaction (Moxa "TCP server" model). Single-client would thrash.
#   - Every 60 s a forced reconcile retries failed port binds.
#   - status.json written ~every 10 s (read by the dashboard badge endpoint).
#   - NEVER use netsh portproxy here (sais-stack.ps1 resets all entries).
#
# Runs as a Windows service via NSSM (LOCAL SYSTEM is fine: COM ports and
# TcpListener are session-independent, no WSL dependency).
#
# NOTE: keep this file ASCII-only (PS 5.1 reads BOM-less files as ANSI) and
# Windows PowerShell 5.1 parse-clean (CI gate in release.yml). The inline C#
# must stay C# 5 compatible (no $"", no ?. operators).

param(
    [string]$InstallDir = "C:\EnvisoftWebX",
    [string]$ConfigDir = ""
)

$ErrorActionPreference = "Continue"

if (-not $ConfigDir) { $ConfigDir = Join-Path $InstallDir "bridge" }
$logDir = Join-Path $InstallDir "logs"
New-Item -ItemType Directory -Force -Path $ConfigDir | Out-Null
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$configPath = Join-Path $ConfigDir "serial-bridge.json"
$statusPath = Join-Path $ConfigDir "status.json"
$logFile = Join-Path $logDir "serial-bridge.log"

function Write-BridgeLog([string]$msg) {
    $line = (Get-Date -Format "yyyy-MM-dd HH:mm:ss") + " " + $msg
    try {
        if ((Test-Path $logFile) -and ((Get-Item $logFile).Length -gt 5MB)) {
            Move-Item -Force $logFile ($logFile + ".old")
        }
        Add-Content -Path $logFile -Value $line -Encoding ASCII
    } catch { }
}

# ---------------------------------------------------------------------------
# Inline C# bridge engine (System.IO.Ports + TcpListener, .NET Framework GAC)
# ---------------------------------------------------------------------------
$bridgeSource = @'
using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Ports;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Threading;

namespace EnvisoftBridge
{
    public class BridgeEntry
    {
        public int ConnectionId;
        public string Name;
        public string ComPort;
        public int Baud;
        public string Parity;
        public int StopBits;
        public int ByteSize;
        public bool XonXoff;
        public bool RtsCts;
        public bool DsrDtr;
        public int TimeoutMs;
        public int ListenPort;

        public string Fingerprint()
        {
            return string.Join("|", new string[] {
                ConnectionId.ToString(), Name, ComPort, Baud.ToString(),
                Parity, StopBits.ToString(), ByteSize.ToString(),
                XonXoff.ToString(), RtsCts.ToString(), DsrDtr.ToString(),
                TimeoutMs.ToString(), ListenPort.ToString() });
        }
    }

    public class SerialTcpBridge
    {
        private const int MaxClients = 8;
        private readonly BridgeEntry cfg;
        private readonly Action<string> log;
        private TcpListener listener;
        private SerialPort serial;
        private readonly object serialLock = new object();
        private readonly List<TcpClient> clients = new List<TcpClient>();
        private volatile bool running;
        private Thread acceptThread;
        private Thread serialThread;
        private long txCount;
        private long errCount;

        public volatile bool ComOpen;
        public string LastError = "";

        public SerialTcpBridge(BridgeEntry entry, Action<string> logger)
        {
            cfg = entry;
            log = logger;
        }

        public BridgeEntry Config { get { return cfg; } }
        public long TxCount { get { return Interlocked.Read(ref txCount); } }
        public long ErrCount { get { return Interlocked.Read(ref errCount); } }
        public int ClientCount { get { lock (clients) { return clients.Count; } } }

        public void Start()
        {
            running = true;
            listener = new TcpListener(IPAddress.Any, cfg.ListenPort);
            try
            {
                listener.Start();
            }
            catch (Exception ex)
            {
                LastError = "listen failed: " + ex.Message;
                running = false;
                listener = null;
                throw;
            }
            serialThread = new Thread(SerialManagerLoop);
            serialThread.IsBackground = true;
            serialThread.Start();
            acceptThread = new Thread(AcceptLoop);
            acceptThread.IsBackground = true;
            acceptThread.Start();
            log("bridge up conn=" + cfg.ConnectionId + " " + cfg.ComPort +
                " -> tcp:" + cfg.ListenPort);
        }

        public void Stop()
        {
            running = false;
            try { if (listener != null) listener.Stop(); } catch (Exception) { }
            listener = null;
            lock (clients)
            {
                foreach (TcpClient c in clients)
                {
                    try { c.Close(); } catch (Exception) { }
                }
                clients.Clear();
            }
            lock (serialLock)
            {
                try { if (serial != null && serial.IsOpen) serial.Close(); }
                catch (Exception) { }
                serial = null;
            }
            ComOpen = false;
        }

        private SerialPort BuildSerialPort()
        {
            Parity p = System.IO.Ports.Parity.None;
            if (cfg.Parity == "O") p = System.IO.Ports.Parity.Odd;
            else if (cfg.Parity == "E") p = System.IO.Ports.Parity.Even;
            StopBits sb = (cfg.StopBits == 2)
                ? System.IO.Ports.StopBits.Two : System.IO.Ports.StopBits.One;
            SerialPort sp = new SerialPort(cfg.ComPort, cfg.Baud, p, cfg.ByteSize, sb);
            if (cfg.RtsCts && cfg.XonXoff) sp.Handshake = Handshake.RequestToSendXOnXOff;
            else if (cfg.RtsCts) sp.Handshake = Handshake.RequestToSend;
            else if (cfg.XonXoff) sp.Handshake = Handshake.XOnXOff;
            else sp.Handshake = Handshake.None;
            // pyserial asserts DTR on open; covers DSR/DTR-powered adapters too.
            sp.DtrEnable = true;
            if (sp.Handshake != Handshake.RequestToSend &&
                sp.Handshake != Handshake.RequestToSendXOnXOff)
            {
                sp.RtsEnable = true;
            }
            sp.WriteTimeout = 2000;
            return sp;
        }

        private void SerialManagerLoop()
        {
            int[] backoff = new int[] { 1000, 2000, 5000, 10000, 30000 };
            int attempt = 0;
            while (running)
            {
                SerialPort current;
                lock (serialLock) { current = serial; }
                if (current == null || !current.IsOpen)
                {
                    ComOpen = false;
                    SerialPort sp = null;
                    try
                    {
                        sp = BuildSerialPort();
                        sp.Open();
                        lock (serialLock) { serial = sp; }
                        ComOpen = true;
                        LastError = "";
                        attempt = 0;
                        log("com open conn=" + cfg.ConnectionId + " " + cfg.ComPort);
                    }
                    catch (Exception ex)
                    {
                        if (sp != null) { try { sp.Dispose(); } catch (Exception) { } }
                        LastError = ex.Message;
                        int idx = (attempt < backoff.Length) ? attempt : backoff.Length - 1;
                        attempt = attempt + 1;
                        int delay = backoff[idx];
                        int waited = 0;
                        while (running && waited < delay)
                        {
                            Thread.Sleep(200);
                            waited = waited + 200;
                        }
                        continue;
                    }
                }
                Thread.Sleep(500);
            }
        }

        private void AcceptLoop()
        {
            while (running)
            {
                TcpClient tcp = null;
                try
                {
                    tcp = listener.AcceptTcpClient();
                }
                catch (Exception)
                {
                    if (!running) return;
                    Thread.Sleep(200);
                    continue;
                }
                try { tcp.NoDelay = true; } catch (Exception) { }
                bool accepted = false;
                lock (clients)
                {
                    // prune clients whose socket died without a clean close
                    for (int i = clients.Count - 1; i >= 0; i--)
                    {
                        if (!clients[i].Connected)
                        {
                            try { clients[i].Close(); } catch (Exception) { }
                            clients.RemoveAt(i);
                        }
                    }
                    if (clients.Count < MaxClients)
                    {
                        clients.Add(tcp);
                        accepted = true;
                    }
                }
                if (!accepted)
                {
                    log("client refused (max " + MaxClients + ") conn=" + cfg.ConnectionId);
                    try { tcp.Close(); } catch (Exception) { }
                    continue;
                }
                TcpClient captured = tcp;
                Thread t = new Thread(delegate() { ClientLoop(captured); });
                t.IsBackground = true;
                t.Start();
            }
        }

        private void ClientLoop(TcpClient tcp)
        {
            byte[] buf = new byte[4096];
            NetworkStream ns = null;
            try
            {
                ns = tcp.GetStream();
                while (running)
                {
                    // 1) wait for a request (idle clients are fine: worker pool
                    //    keeps sockets open between polling cycles)
                    ns.ReadTimeout = Timeout.Infinite;
                    int n;
                    try { n = ns.Read(buf, 0, buf.Length); }
                    catch (Exception) { break; }
                    if (n <= 0) break;
                    MemoryStream req = new MemoryStream();
                    req.Write(buf, 0, n);
                    // assemble: pymodbus sends the whole ADU in one send(), but
                    // allow 20 ms for fragmented writes
                    ns.ReadTimeout = 20;
                    try
                    {
                        while ((n = ns.Read(buf, 0, buf.Length)) > 0)
                        {
                            req.Write(buf, 0, n);
                        }
                        if (n == 0) break;  // client closed mid-request
                    }
                    catch (IOException) { }  // timeout = frame complete

                    // 2) serialized transaction on the shared serial port;
                    //    the response goes ONLY to this client
                    byte[] reqBytes = req.ToArray();
                    lock (serialLock)
                    {
                        SerialPort sp = serial;
                        if (sp == null || !sp.IsOpen)
                        {
                            Interlocked.Increment(ref errCount);
                            continue;  // client times out on its own timeout_ms
                        }
                        try
                        {
                            sp.DiscardInBuffer();  // drop stale/unsolicited bytes
                            sp.Write(reqBytes, 0, reqBytes.Length);
                            // 3.5 char times at 11 bits/char, floor 20 ms
                            int idleGap = 38500 / Math.Max(cfg.Baud, 300) + 5;
                            if (idleGap < 20) idleGap = 20;
                            int total = Math.Max(cfg.TimeoutMs, 500);
                            int deadline = Environment.TickCount + total;
                            bool gotAny = false;
                            while (true)
                            {
                                int remaining = deadline - Environment.TickCount;
                                if (remaining <= 0) break;
                                sp.ReadTimeout = gotAny ? idleGap : remaining;
                                int r;
                                try { r = sp.Read(buf, 0, buf.Length); }
                                catch (TimeoutException) { break; }
                                if (r <= 0) break;
                                gotAny = true;
                                ns.Write(buf, 0, r);
                            }
                            if (gotAny) Interlocked.Increment(ref txCount);
                            else Interlocked.Increment(ref errCount);
                        }
                        catch (Exception ex)
                        {
                            // serial died mid-transaction -> manager loop reopens
                            LastError = ex.Message;
                            ComOpen = false;
                            Interlocked.Increment(ref errCount);
                            try { sp.Close(); } catch (Exception) { }
                        }
                    }
                }
            }
            catch (Exception) { }
            finally
            {
                lock (clients) { clients.Remove(tcp); }
                try { tcp.Close(); } catch (Exception) { }
            }
        }
    }

    public class BridgeManager
    {
        private readonly Dictionary<int, SerialTcpBridge> bridges =
            new Dictionary<int, SerialTcpBridge>();
        private readonly Dictionary<int, BridgeEntry> failedEntries =
            new Dictionary<int, BridgeEntry>();
        private readonly Dictionary<int, string> failedErrors =
            new Dictionary<int, string>();
        private readonly Queue<string> logQueue = new Queue<string>();
        private readonly object logLock = new object();

        private void Log(string msg)
        {
            lock (logLock)
            {
                logQueue.Enqueue(msg);
                while (logQueue.Count > 500) logQueue.Dequeue();
            }
        }

        public string[] DrainLog()
        {
            lock (logLock)
            {
                string[] arr = logQueue.ToArray();
                logQueue.Clear();
                return arr;
            }
        }

        public int Count { get { return bridges.Count; } }

        public void Reconcile(BridgeEntry[] entries)
        {
            Dictionary<int, BridgeEntry> want = new Dictionary<int, BridgeEntry>();
            foreach (BridgeEntry e in entries) want[e.ConnectionId] = e;

            // stop removed or changed bridges
            List<int> toRemove = new List<int>();
            foreach (KeyValuePair<int, SerialTcpBridge> kv in bridges)
            {
                BridgeEntry w;
                if (!want.TryGetValue(kv.Key, out w) ||
                    w.Fingerprint() != kv.Value.Config.Fingerprint())
                {
                    toRemove.Add(kv.Key);
                }
            }
            foreach (int id in toRemove)
            {
                Log("bridge stop conn=" + id);
                bridges[id].Stop();
                bridges.Remove(id);
            }

            // start new / changed / previously failed
            foreach (KeyValuePair<int, BridgeEntry> kv in want)
            {
                if (bridges.ContainsKey(kv.Key)) continue;
                SerialTcpBridge b = new SerialTcpBridge(kv.Value, Log);
                try
                {
                    b.Start();
                    bridges[kv.Key] = b;
                    failedEntries.Remove(kv.Key);
                    failedErrors.Remove(kv.Key);
                }
                catch (Exception ex)
                {
                    b.Stop();
                    failedEntries[kv.Key] = kv.Value;
                    failedErrors[kv.Key] = ex.Message;
                    Log("bridge start FAILED conn=" + kv.Key + ": " + ex.Message);
                }
            }

            // drop failure records for entries no longer wanted
            List<int> staleFail = new List<int>();
            foreach (int id in failedEntries.Keys)
            {
                if (!want.ContainsKey(id)) staleFail.Add(id);
            }
            foreach (int id in staleFail)
            {
                failedEntries.Remove(id);
                failedErrors.Remove(id);
            }
        }

        public string GetStatusJson()
        {
            StringBuilder sb = new StringBuilder();
            sb.Append("{\"ts\":\"");
            sb.Append(DateTime.UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ"));
            sb.Append("\",\"bridges\":[");
            bool first = true;
            foreach (KeyValuePair<int, SerialTcpBridge> kv in bridges)
            {
                if (!first) sb.Append(",");
                first = false;
                SerialTcpBridge b = kv.Value;
                sb.Append("{\"connection_id\":").Append(kv.Key);
                sb.Append(",\"com_port\":\"").Append(JsonEscape(b.Config.ComPort)).Append("\"");
                sb.Append(",\"listen_port\":").Append(b.Config.ListenPort);
                sb.Append(",\"com_open\":").Append(b.ComOpen ? "true" : "false");
                sb.Append(",\"client_count\":").Append(b.ClientCount);
                sb.Append(",\"tx_count\":").Append(b.TxCount);
                sb.Append(",\"err_count\":").Append(b.ErrCount);
                sb.Append(",\"last_error\":\"").Append(JsonEscape(b.LastError)).Append("\"}");
            }
            foreach (KeyValuePair<int, BridgeEntry> kv in failedEntries)
            {
                if (!first) sb.Append(",");
                first = false;
                string err;
                failedErrors.TryGetValue(kv.Key, out err);
                sb.Append("{\"connection_id\":").Append(kv.Key);
                sb.Append(",\"com_port\":\"").Append(JsonEscape(kv.Value.ComPort)).Append("\"");
                sb.Append(",\"listen_port\":").Append(kv.Value.ListenPort);
                sb.Append(",\"com_open\":false,\"client_count\":0");
                sb.Append(",\"tx_count\":0,\"err_count\":0");
                sb.Append(",\"last_error\":\"").Append(JsonEscape(err)).Append("\"}");
            }
            sb.Append("]}");
            return sb.ToString();
        }

        private static string JsonEscape(string s)
        {
            if (s == null) return "";
            StringBuilder sb = new StringBuilder();
            foreach (char c in s)
            {
                if (c == '"' || c == '\\') { sb.Append('\\'); sb.Append(c); }
                else if (c < ' ') sb.Append(' ');
                else sb.Append(c);
            }
            return sb.ToString();
        }

        public void StopAll()
        {
            foreach (SerialTcpBridge b in bridges.Values) b.Stop();
            bridges.Clear();
            failedEntries.Clear();
            failedErrors.Clear();
        }
    }
}
'@

try {
    Add-Type -TypeDefinition $bridgeSource -ReferencedAssemblies @("System.dll") -ErrorAction Stop
} catch {
    Write-BridgeLog ("FATAL: Add-Type failed: " + $_.Exception.Message)
    exit 1
}

$mgr = New-Object EnvisoftBridge.BridgeManager
$lastWrite = [DateTime]::MinValue
$lastEntries = New-Object "System.Collections.Generic.List[EnvisoftBridge.BridgeEntry]"
$lastForce = Get-Date
$loopCount = 0

Write-BridgeLog ("serial-bridge starting, config: " + $configPath)

while ($true) {
    try {
        $configChanged = $false
        if (Test-Path $configPath) {
            $mtime = (Get-Item $configPath).LastWriteTimeUtc
            if ($mtime -ne $lastWrite) {
                try {
                    $raw = [System.IO.File]::ReadAllText($configPath)
                    $cfg = $raw | ConvertFrom-Json
                    $entries = New-Object "System.Collections.Generic.List[EnvisoftBridge.BridgeEntry]"
                    foreach ($b in @($cfg.bridges)) {
                        if ($null -eq $b) { continue }
                        $e = New-Object EnvisoftBridge.BridgeEntry
                        $e.ConnectionId = [int]$b.connection_id
                        $e.Name = [string]$b.name
                        $e.ComPort = [string]$b.com_port
                        $e.Baud = [int]$b.baudrate
                        $e.Parity = [string]$b.parity
                        $e.StopBits = [int]$b.stopbits
                        $e.ByteSize = [int]$b.bytesize
                        $e.XonXoff = [bool]$b.xonxoff
                        $e.RtsCts = [bool]$b.rtscts
                        $e.DsrDtr = [bool]$b.dsrdtr
                        $e.TimeoutMs = [int]$b.timeout_ms
                        $e.ListenPort = [int]$b.listen_port
                        $entries.Add($e)
                    }
                    $lastEntries = $entries
                    $lastWrite = $mtime
                    $configChanged = $true
                    Write-BridgeLog ("config loaded: " + $entries.Count + " bridge(s)")
                } catch {
                    # half-written/corrupt file: keep last-known-good state
                    Write-BridgeLog ("config parse FAILED (keeping state): " + $_.Exception.Message)
                }
            }
        }

        # forced reconcile every 60 s retries failed port binds
        $force = ((Get-Date) - $lastForce).TotalSeconds -ge 60
        if ($configChanged -or $force) {
            $mgr.Reconcile($lastEntries.ToArray())
            $lastForce = Get-Date
        }

        # status.json every ~10 s (2 x 5 s loop)
        $loopCount++
        if (($loopCount % 2) -eq 0) {
            try {
                $json = $mgr.GetStatusJson()
                $tmp = $statusPath + ".tmp"
                $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
                [System.IO.File]::WriteAllText($tmp, $json, $utf8NoBom)
                Move-Item -Force $tmp $statusPath
            } catch {
                Write-BridgeLog ("status write failed: " + $_.Exception.Message)
            }
        }

        foreach ($line in $mgr.DrainLog()) { Write-BridgeLog $line }
    } catch {
        Write-BridgeLog ("loop error: " + $_.Exception.Message)
    }
    Start-Sleep -Seconds 5
}
