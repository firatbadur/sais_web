"""Connection.protocol → uygun ProtocolWriter."""
from .ascii_custom import AsciiCustomWriter
from .base import ProtocolWriter
from .modbus_serial import ModbusSerialWriter
from .modbus_tcp import ModbusTcpWriter


_WRITERS = {
    "modbus_tcp": ModbusTcpWriter,
    "modbus_rtu": ModbusSerialWriter,
    "modbus_ascii": ModbusSerialWriter,
    "modbus_rtu_over_tcp": ModbusTcpWriter,
    "modbus_ascii_over_tcp": ModbusTcpWriter,
    "ascii_custom": AsciiCustomWriter,
}


def build_writer(connection) -> ProtocolWriter:
    from ..bridge_redirect import maybe_redirect
    connection = maybe_redirect(connection)
    cls = _WRITERS.get(connection.protocol)
    if cls is None:
        raise ValueError(f"Desteklenmeyen protokol: {connection.protocol}")
    return cls(connection)
