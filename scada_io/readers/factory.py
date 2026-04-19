"""Connection.protocol → uygun ProtocolReader."""
from .ascii_custom import AsciiCustomReader
from .base import ProtocolReader
from .modbus_serial import ModbusSerialReader
from .modbus_tcp import ModbusTcpReader


_READERS = {
    "modbus_tcp": ModbusTcpReader,
    "modbus_rtu": ModbusSerialReader,
    "modbus_ascii": ModbusSerialReader,
    "ascii_custom": AsciiCustomReader,
}


def build_reader(connection) -> ProtocolReader:
    """Connection.protocol bazlı reader oluştur.

    Bilinmeyen protokol için ValueError fırlatır — `dispatch_polls`
    çağıran tarafta yakalanır ve connection error olarak işaretlenir.
    """
    cls = _READERS.get(connection.protocol)
    if cls is None:
        raise ValueError(f"Desteklenmeyen protokol: {connection.protocol}")
    return cls(connection)
