import random

class Hamming74:
    @staticmethod
    def encode_nibble(nibble):
        """Codifica 4 bits de datos en 7 bits usando Hamming (7,4)."""
        d1, d2, d3, d4 = nibble
        p1 = d1 ^ d2 ^ d4
        p2 = d1 ^ d3 ^ d4
        p3 = d2 ^ d3 ^ d4
        return [p1, p2, d1, p3, d2, d3, d4]

    @staticmethod
    def decode_nibble(block):
        """Decodifica 7 bits, corrige 1 bit de error si existe, y extrae los 4 bits de datos."""
        p1, p2, d1, p3, d2, d3, d4 = block
        
        # Calcular los bits de paridad (síndromes)
        s1 = p1 ^ d1 ^ d2 ^ d4
        s2 = p2 ^ d1 ^ d3 ^ d4
        s3 = p3 ^ d2 ^ d3 ^ d4
        
        syndrome = (s3 << 2) | (s2 << 1) | s1
        
        # Corregir si hay error (syndrome > 0)
        if syndrome > 0:
            error_index = syndrome - 1
            block[error_index] ^= 1
            
        return [block[2], block[4], block[5], block[6]]

    @staticmethod
    def string_to_bits(s):
        """Convierte un string UTF-8 a una lista de bits."""
        bits = []
        for char in s.encode('utf-8'):
            for i in range(7, -1, -1):
                bits.append((char >> i) & 1)
        return bits

    @staticmethod
    def bits_to_string(bits):
        """Convierte una lista de bits de vuelta a un string UTF-8."""
        chars = bytearray()
        for i in range(0, len(bits), 8):
            byte = bits[i:i+8]
            if len(byte) < 8:
                break
            value = int(''.join(map(str, byte)), 2)
            chars.append(value)
        return chars.decode('utf-8', errors='ignore')

    @classmethod
    def encode_message(cls, message_str, bit_flip_prob=0.0):
        """Aplica Hamming (7,4) a todo el mensaje y opcionalmente inyecta ruido."""
        data_bits = cls.string_to_bits(message_str)
        encoded_bits = []
        
        # Rellenar con ceros si no es múltiplo de 4
        while len(data_bits) % 4 != 0:
            data_bits.append(0)
            
        for i in range(0, len(data_bits), 4):
            nibble = data_bits[i:i+4]
            encoded_block = cls.encode_nibble(nibble)
            
            # Inyectar ruido según la probabilidad
            if bit_flip_prob > 0:
                for j in range(len(encoded_block)):
                    if random.random() < bit_flip_prob:
                        encoded_block[j] ^= 1
                        
            encoded_bits.extend(encoded_block)
            
        return ''.join(map(str, encoded_bits))

    @classmethod
    def decode_message(cls, encoded_bit_string):
        """Recibe una cadena de bits, corrige errores por bloque y devuelve el string."""
        encoded_bits = [int(b) for b in encoded_bit_string]
        decoded_bits = []
        
        for i in range(0, len(encoded_bits), 7):
            block = encoded_bits[i:i+7]
            if len(block) < 7:
                break
            decoded_nibble = cls.decode_nibble(block)
            decoded_bits.extend(decoded_nibble)
            
        return cls.bits_to_string(decoded_bits)