"""Connection.protocol → uygun ProtocolWriter."""
from .ascii_custom import AsciiCustomWriter
from .base import ProtocolWriter
from .modbus_serial import ModbusSerialWriter
from .modbus_tcp import ModbusTcpWriter


_WRITERS = {
    "modbus_tcp": ModbusTcpWriter,
    "modbus_rtu": ModbusSerialWriter,
    "modbus_ascii": ModbusSerialWriter,
    "ascii_custom": AsciiCustomWriter,
}


def build_writer(connection) -> ProtocolWriter:
    cls = _WRITERS.get(connection.protocol)
    if cls is None:
        raise ValueError(f"Desteklenmeyen protokol: {connection.protocol}")
    return cls(connection)
