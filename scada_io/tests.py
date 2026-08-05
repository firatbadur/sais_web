"""scada_io decoder/encoder unit testleri."""
import struct

from django.test import SimpleTestCase

from .comm_errors import (
    CONN_OPEN,
    CONN_RESET,
    CRC_FRAME,
    DECODE,
    ILLEGAL_ADDRESS,
    ILLEGAL_FUNCTION,
    ILLEGAL_VALUE,
    SLAVE_FAILURE,
    TIMEOUT,
    UNKNOWN,
    category_source,
    classify_comm_error,
    verdict,
)
from .decoders import decode_registers, encode_value


def _to_regs(packed: bytes, byte_endian: str = ">") -> list[int]:
    """Helper: byte string'i 16-bit register listesine böler."""
    if len(packed) % 2:
        packed += b"\x00"
    return [
        struct.unpack(byte_endian + "H", packed[i:i + 2])[0]
        for i in range(0, len(packed), 2)
    ]


class DecoderTests(SimpleTestCase):
    # ---- int16 / uint16 ----

    def test_uint16_basic(self):
        self.assertEqual(decode_registers([1234], "uint16"), 1234)

    def test_int16_negative(self):
        # 0xFFFE → -2 signed
        self.assertEqual(decode_registers([0xFFFE], "int16"), -2)

    # ---- 32-bit ----

    def test_float32_big_word_big_byte(self):
        regs = _to_regs(struct.pack(">f", 12.34), ">")
        self.assertAlmostEqual(
            decode_registers(regs, "float32", byte_order="big", word_order="big"),
            12.34, places=4,
        )

    def test_float32_word_swap_little(self):
        # Word-swap: caller reverse'ediyor; decoder da reverse'leyince orijinal'e dönmeli
        regs = _to_regs(struct.pack(">f", 56.78), ">")
        swapped = list(reversed(regs))
        self.assertAlmostEqual(
            decode_registers(swapped, "float32", byte_order="big", word_order="little"),
            56.78, places=4,
        )

    def test_int32_big_word(self):
        regs = _to_regs(struct.pack(">i", -1234567), ">")
        self.assertEqual(
            decode_registers(regs, "int32", byte_order="big", word_order="big"),
            -1234567,
        )

    def test_uint32_round_trip(self):
        original = 4_000_000_000
        regs = encode_value(original, "uint32", byte_order="big", word_order="big")
        self.assertEqual(
            decode_registers(regs, "uint32", byte_order="big", word_order="big"),
            original,
        )

    # ---- 64-bit ----

    def test_float64_round_trip(self):
        original = 3.141592653589793
        regs = encode_value(original, "float64", byte_order="big", word_order="big")
        self.assertEqual(len(regs), 4)
        self.assertAlmostEqual(
            decode_registers(regs, "float64", byte_order="big", word_order="big"),
            original, places=12,
        )

    def test_int64_round_trip(self):
        original = -987_654_321_098_765
        regs = encode_value(original, "int64", byte_order="big", word_order="big")
        self.assertEqual(
            decode_registers(regs, "int64", byte_order="big", word_order="big"),
            original,
        )

    # ---- bool / bit ----

    def test_bool_true(self):
        self.assertTrue(decode_registers([1], "bool"))

    def test_bool_false(self):
        self.assertFalse(decode_registers([0], "bool"))

    def test_bit_extraction(self):
        # 0b10101010 = 170; bit 1 = True, bit 2 = False, bit 7 = True
        self.assertTrue(decode_registers([0b10101010], "bit", bit_position=1))
        self.assertFalse(decode_registers([0b10101010], "bit", bit_position=2))
        self.assertTrue(decode_registers([0b10101010], "bit", bit_position=7))

    def test_bit_set_preserves_other_bits(self):
        # Mevcut register: 0b10100000; bit 1 set edilirse → 0b10100010
        regs = encode_value(True, "bit", bit_position=1, current_register=0b10100000)
        self.assertEqual(regs[0], 0b10100010)

    def test_bit_clear_preserves_other_bits(self):
        # Mevcut register: 0b10100010; bit 1 clear → 0b10100000
        regs = encode_value(False, "bit", bit_position=1, current_register=0b10100010)
        self.assertEqual(regs[0], 0b10100000)

    # ---- string ----

    def test_string_round_trip(self):
        s = "OK"
        regs = encode_value(s, "string", byte_order="big", word_order="big")
        decoded = decode_registers(regs, "string", byte_order="big", word_order="big")
        self.assertEqual(decoded, s)

    def test_string_strip_null_bytes(self):
        # "AB\0\0" → "AB"
        regs = [ord("A") << 8 | ord("B"), 0]
        self.assertEqual(
            decode_registers(regs, "string", byte_order="big", word_order="big"),
            "AB",
        )

    # ---- byte_order / word_order kombinasyonları ----
    #
    # byte_order = register İÇİ byte sırası (little = byte swap),
    # word_order = register sırası (little = word swap). float32 için
    # 4 wire formatı: ABCD=big/big, CDAB=big/little, BADC=little/big,
    # DCBA=little/little (Modbus Poll "Little-endian").

    def test_float32_dcba_little_little(self):
        # Wire DCBA: tam ters çevrilmiş byte dizisi (Modbus Poll "Little-endian")
        be = struct.pack(">f", 7.25)          # A B C D
        dcba = be[::-1]                        # D C B A
        regs = _to_regs(dcba, ">")             # [DC, BA]
        self.assertAlmostEqual(
            decode_registers(regs, "float32", byte_order="little", word_order="little"),
            7.25, places=4,
        )

    def test_float32_badc_little_big(self):
        # Wire BADC: her word içinde byte swap, word sırası doğal
        be = struct.pack(">f", 1234.5)         # A B C D
        badc = bytes([be[1], be[0], be[3], be[2]])
        regs = _to_regs(badc, ">")             # [BA, DC]
        self.assertAlmostEqual(
            decode_registers(regs, "float32", byte_order="little", word_order="big"),
            1234.5, places=3,
        )

    def test_float32_all_orders_round_trip(self):
        for bo in ("big", "little"):
            for wo in ("big", "little"):
                regs = encode_value(-42.75, "float32", byte_order=bo, word_order=wo)
                self.assertAlmostEqual(
                    decode_registers(regs, "float32", byte_order=bo, word_order=wo),
                    -42.75, places=4, msg=f"{bo}/{wo}",
                )

    def test_uint16_byte_swap(self):
        # Tek register'da byte_order='little' iki byte'ı takas eder
        self.assertEqual(
            decode_registers([0x1234], "uint16", byte_order="little"), 0x3412
        )
        self.assertEqual(
            decode_registers([0x1234], "uint16", byte_order="big"), 0x1234
        )

    def test_int32_word_swap_round_trip(self):
        original = 1_000_000
        # Encode big-word, decode big-word → original
        regs_big = encode_value(original, "int32", byte_order="big", word_order="big")
        self.assertEqual(decode_registers(regs_big, "int32", byte_order="big", word_order="big"), original)
        # Encode little-word, decode little-word → original (swap kendi tersi)
        regs_lit = encode_value(original, "int32", byte_order="big", word_order="little")
        self.assertEqual(decode_registers(regs_lit, "int32", byte_order="big", word_order="little"), original)
        # Big ile encode'lanan, little ile decode'lanırsa farklı çıkmalı
        self.assertNotEqual(decode_registers(regs_big, "int32", byte_order="big", word_order="little"), original)

    # ---- error cases ----

    def test_unknown_data_type_raises(self):
        with self.assertRaises(ValueError):
            decode_registers([1], "qbert")

    def test_bit_without_position_raises(self):
        with self.assertRaises(ValueError):
            decode_registers([1], "bit")

    def test_int32_insufficient_registers_raises(self):
        with self.assertRaises(ValueError):
            decode_registers([1], "int32")


class ClassifyCommErrorTests(SimpleTestCase):
    """İletişim hata metni → kategori sınıflandırması."""

    def test_empty_is_unknown(self):
        self.assertEqual(classify_comm_error(""), UNKNOWN)
        self.assertEqual(classify_comm_error(None), UNKNOWN)

    # ---- Modbus ExceptionResponse (cihaz reddi = bizim config) ----

    def test_exception_code_illegal_function(self):
        s = "Modbus hata: ExceptionResponse(dev_id=1, function_code=131, exception_code=1)"
        self.assertEqual(classify_comm_error(s), ILLEGAL_FUNCTION)

    def test_exception_code_illegal_address(self):
        s = "Modbus hata: ExceptionResponse(dev_id=1, function_code=131, exception_code=2)"
        self.assertEqual(classify_comm_error(s), ILLEGAL_ADDRESS)
        self.assertEqual(category_source(ILLEGAL_ADDRESS), "config")

    def test_exception_code_illegal_value(self):
        s = "Modbus hata: ExceptionResponse(dev_id=1, function_code=131, exception_code=3)"
        self.assertEqual(classify_comm_error(s), ILLEGAL_VALUE)

    def test_exception_code_slave_failure(self):
        s = "Modbus hata: ExceptionResponse(dev_id=1, function_code=132, exception_code=4)"
        self.assertEqual(classify_comm_error(s), SLAVE_FAILURE)
        self.assertEqual(category_source(SLAVE_FAILURE), "device")

    def test_illegal_by_name(self):
        self.assertEqual(classify_comm_error("Illegal Data Address"), ILLEGAL_ADDRESS)

    # ---- timeout / no response (ambiguous) ----

    def test_no_response_is_timeout(self):
        s = "ModbusIOException: Modbus Error: [Input/Output] No Response received from the remote unit"
        self.assertEqual(classify_comm_error(s), TIMEOUT)
        self.assertEqual(category_source(TIMEOUT), "ambiguous")

    def test_generic_io_is_timeout(self):
        self.assertEqual(
            classify_comm_error("Modbus Error: [Input/Output] "), TIMEOUT
        )

    def test_ascii_timeout(self):
        self.assertEqual(classify_comm_error("cihazdan yanıt yok (timeout)"), TIMEOUT)

    # ---- bağlantı açma / kopma (network) ----

    def test_conn_open(self):
        self.assertEqual(classify_comm_error("bağlantı açılamadı"), CONN_OPEN)
        self.assertEqual(
            classify_comm_error("TCP bağlantısı kurulamadı: 10.0.0.5:502"), CONN_OPEN
        )

    def test_conn_refused(self):
        self.assertEqual(
            classify_comm_error("ConnectionRefusedError: [WinError 10061] ..."), CONN_OPEN
        )

    def test_conn_reset(self):
        self.assertEqual(
            classify_comm_error("ConnectionResetError: [WinError 10054] ..."), CONN_RESET
        )
        # Oturum ortasında düşen bağlantı → "[connection]" → reset.
        self.assertEqual(
            classify_comm_error("ConnectionException: Modbus Error: [Connection] lost"),
            CONN_RESET,
        )
        self.assertEqual(category_source(CONN_RESET), "network")

    def test_bağlantı_yok_is_conn_open(self):
        self.assertEqual(classify_comm_error("bağlantı yok"), CONN_OPEN)

    # ---- crc / frame (line) ----

    def test_unable_to_decode_is_crc_frame(self):
        s = "ModbusIOException: Modbus Error: [Input/Output] Unable to decode response"
        self.assertEqual(classify_comm_error(s), CRC_FRAME)

    def test_crc(self):
        self.assertEqual(classify_comm_error("CRC check failed"), CRC_FRAME)

    # ---- decode / parse (bizim config) ----

    def test_decode_insufficient_registers(self):
        s = "decode: int32 2 register gerektirir; 1 verildi"
        self.assertEqual(classify_comm_error(s), DECODE)
        self.assertEqual(category_source(DECODE), "config")

    def test_regex_no_match(self):
        self.assertEqual(classify_comm_error("regex eşleşmedi"), DECODE)

    def test_unknown_fallback(self):
        self.assertEqual(classify_comm_error("something totally unexpected xyz"), UNKNOWN)


class VerdictTests(SimpleTestCase):
    """Bağlantı-seviyesi vs sensör-seviyesi kararı."""

    def test_healthy(self):
        text, level = verdict(0.001, [0.0, 0.0, 0.002])
        self.assertEqual(level, "ok")

    def test_mikrodev_episodic_socket_drop(self):
        """Gerçek saha: tüm sensörler aynı anda düşüyor, oran düşük (%2), olaylar
        %100 conn_reset. Eski heuristik 'Karışık' diyordu — artık bağlantı seviyesi."""
        rates = [0.02] * 30          # hepsi birlikte, benzer oran
        cats = {CONN_RESET: 100}
        text, level = verdict(0.02, rates, cats)
        self.assertEqual(level, "err")
        self.assertIn("Bağlantı seviyesi", text)

    def test_iskenderun_specific_sensors(self):
        """Gerçek saha: belirli dijital noktalar hep bad, analoglar temiz →
        bizim config şüphesi (CommErrorEvent verisi henüz yok)."""
        rates = [0.61, 0.59, 0.58] + [0.0] * 16
        text, level = verdict(0.035, rates)
        self.assertEqual(level, "warn")
        self.assertIn("Sensör seviyesi", text)

    def test_category_config_dominant_wins(self):
        """Olaylar cihaz reddi (illegal_address) baskınsa → config şüphesi."""
        cats = {ILLEGAL_ADDRESS: 90, TIMEOUT: 10}
        text, level = verdict(0.4, [0.4] * 5, cats)
        self.assertEqual(level, "warn")
        self.assertIn("Sensör seviyesi", text)

    def test_all_sensors_dead_uniform(self):
        """PLC tamamen erişilemez → herkes %100 → bağlantı seviyesi."""
        text, level = verdict(1.0, [1.0] * 12)
        self.assertEqual(level, "err")
        self.assertIn("Bağlantı seviyesi", text)

    def test_ambiguous_falls_back_to_mixed(self):
        """Ne kategori baskın ne de net bir patern → Karışık."""
        rates = [0.3, 0.12]  # spread 0.18 > 0.10; bad yok (>=0.5), clean yok (<0.05)
        text, level = verdict(0.2, rates)
        self.assertEqual(level, "warn")
        self.assertIn("Karışık", text)


# ---------------------------------------------------------------------------
# Seri köprü redirect (bridge_redirect + factory hook)
# ---------------------------------------------------------------------------

from unittest import mock  # noqa: E402

from django.test import override_settings  # noqa: E402

from api.models import Connection  # noqa: E402

from .bridge_redirect import maybe_redirect  # noqa: E402
from .readers.ascii_custom import AsciiCustomReader  # noqa: E402
from .readers.factory import build_reader  # noqa: E402
from .readers.modbus_serial import ModbusSerialReader  # noqa: E402
from .readers.modbus_tcp import ModbusTcpReader  # noqa: E402
from .writers.ascii_custom import AsciiCustomWriter  # noqa: E402
from .writers.factory import build_writer  # noqa: E402
from .writers.modbus_tcp import ModbusTcpWriter  # noqa: E402


def _serial_conn(pk=5, protocol="modbus_rtu", **kw):
    """Kaydedilmemiş (in-memory) seri Connection."""
    defaults = dict(
        id=pk, name="test", protocol=protocol, transport="serial",
        serial_port="COM5", baudrate=9600, parity=0, stop_bits=1, byte_size=8,
        timeout_ms=2000, retry_count=3,
    )
    defaults.update(kw)
    return Connection(**defaults)


@override_settings(SERIAL_BRIDGE_HOST="172.20.240.1", SERIAL_BRIDGE_PORT_BASE=8900)
class BridgeRedirectTests(SimpleTestCase):
    def test_rtu_redirected_to_tcp_reader(self):
        conn = _serial_conn(pk=5, protocol="modbus_rtu")
        reader = build_reader(conn)
        self.assertIsInstance(reader, ModbusTcpReader)
        self.assertEqual(reader.connection.host, "172.20.240.1")
        self.assertEqual(reader.connection.port, 8905)
        self.assertEqual(reader.connection.protocol, "modbus_rtu_over_tcp")
        self.assertEqual(reader.connection.transport, "tcp")

    def test_original_instance_untouched(self):
        conn = _serial_conn(pk=5, protocol="modbus_rtu")
        build_reader(conn)
        self.assertEqual(conn.protocol, "modbus_rtu")
        self.assertEqual(conn.transport, "serial")
        self.assertEqual(conn.serial_port, "COM5")
        self.assertEqual(conn.host, "")

    def test_ascii_maps_to_ascii_over_tcp(self):
        reader = build_reader(_serial_conn(pk=7, protocol="modbus_ascii"))
        self.assertIsInstance(reader, ModbusTcpReader)
        self.assertEqual(reader.connection.protocol, "modbus_ascii_over_tcp")
        self.assertEqual(reader.connection.port, 8907)

    def test_ascii_custom_keeps_class_flips_transport(self):
        reader = build_reader(_serial_conn(pk=9, protocol="ascii_custom"))
        self.assertIsInstance(reader, AsciiCustomReader)
        self.assertEqual(reader.connection.transport, "tcp")
        self.assertEqual(reader.connection.host, "172.20.240.1")
        self.assertEqual(reader.connection.port, 8909)

    def test_large_pk_modulo(self):
        bridged = maybe_redirect(_serial_conn(pk=70005))
        self.assertEqual(bridged.port, 8905)

    def test_tcp_connection_not_redirected(self):
        conn = Connection(
            id=3, name="t", protocol="modbus_tcp", transport="tcp",
            host="10.0.0.5", port=502,
        )
        self.assertIs(maybe_redirect(conn), conn)

    def test_writer_redirected(self):
        writer = build_writer(_serial_conn(pk=5, protocol="modbus_rtu"))
        self.assertIsInstance(writer, ModbusTcpWriter)
        self.assertEqual(writer.connection.port, 8905)
        self.assertEqual(writer.connection.protocol, "modbus_rtu_over_tcp")

    def test_writer_ascii_custom(self):
        writer = build_writer(_serial_conn(pk=9, protocol="ascii_custom"))
        self.assertIsInstance(writer, AsciiCustomWriter)
        self.assertEqual(writer.connection.transport, "tcp")


@override_settings(SERIAL_BRIDGE_HOST="")
class BridgeRedirectDisabledTests(SimpleTestCase):
    def test_identity_when_host_empty(self):
        conn = _serial_conn()
        self.assertIs(maybe_redirect(conn), conn)
        reader = build_reader(conn)
        self.assertIsInstance(reader, ModbusSerialReader)
        self.assertIs(reader.connection, conn)


class AsciiSocketUrlTests(SimpleTestCase):
    def test_tcp_transport_uses_socket_url(self):
        conn = Connection(
            id=4, name="t", protocol="ascii_custom", transport="tcp",
            host="10.1.1.5", port=8907, timeout_ms=2000,
        )
        fake = mock.MagicMock()
        fake.is_open = True
        with mock.patch("serial.serial_for_url", return_value=fake) as sfu, \
                mock.patch("serial.Serial") as ser:
            reader = AsciiCustomReader(conn)
            self.assertTrue(reader.open())
            sfu.assert_called_once()
            self.assertEqual(sfu.call_args[0][0], "socket://10.1.1.5:8907")
            ser.assert_not_called()

    def test_serial_transport_uses_serial(self):
        conn = _serial_conn(pk=4, protocol="ascii_custom")
        fake = mock.MagicMock()
        fake.is_open = True
        with mock.patch("serial.Serial", return_value=fake) as ser, \
                mock.patch("serial.serial_for_url") as sfu:
            reader = AsciiCustomReader(conn)
            self.assertTrue(reader.open())
            ser.assert_called_once()
            sfu.assert_not_called()
