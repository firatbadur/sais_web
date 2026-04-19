"""scada_io decoder/encoder unit testleri."""
import struct

from django.test import SimpleTestCase

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

    # ---- byte_order combinations ----
    #
    # Not: byte_order tek-register tipler için (uint16/int16/bool/bit) etkisizdir
    # çünkü pymodbus zaten register'ı integer olarak çözmüş olur. byte_order
    # multi-register tiplerde (float32, int32 vb.) anlam kazanır — float32
    # word_swap testi byte_order davranışını implicit olarak test eder.

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
