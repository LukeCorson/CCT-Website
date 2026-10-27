// Wire compatible with the original CCT stable_uart protocol (v1).
#include "stable_uart.h"
namespace Uart {
namespace {
uint16_t addCrc(uint16_t crc, uint8_t value) {
  crc ^= uint16_t(value) << 8;
  for (uint8_t bit = 0; bit < 8; ++bit)
    crc = crc & 0x8000 ? uint16_t((crc << 1) ^ 0x1021) : uint16_t(crc << 1);
  return crc;
}
}
uint16_t crc16(const uint8_t *data, size_t length) {
  uint16_t crc = 0xFFFF;
  while (length--) crc = addCrc(crc, *data++);
  return crc;
}
bool send(Print &port, Type type, uint8_t sequence, const uint8_t *payload, uint8_t length) {
  if (length > MAX_PAYLOAD) return false;
  uint8_t data[MAX_FRAME] = {SOF1, SOF2, VERSION, uint8_t(type), sequence, length};
  memcpy(data + 6, payload, length);
  const uint16_t crc = crc16(data + 2, length + 4);
  data[length + 6] = crc;
  data[length + 7] = crc >> 8;
  return port.write(data, length + 8) == length + 8;
}
uint16_t read16(const uint8_t *data) { return data[0] | uint16_t(data[1]) << 8; }
uint32_t read32(const uint8_t *data) { return read16(data) | uint32_t(read16(data + 2)) << 16; }
void write16(uint8_t *data, uint16_t value) { data[0] = value; data[1] = value >> 8; }
void write32(uint8_t *data, uint32_t value) { write16(data, value); write16(data + 2, value >> 16); }
void Parser::reset() { state_ = Sof1; index_ = 0; crc_ = 0xFFFF; receivedCrc_ = 0; }
Result Parser::push(uint8_t byte, Frame &frame) {
  switch (state_) {
    case Sof1: if (byte == SOF1) state_ = Sof2; break;
    case Sof2: state_ = byte == SOF2 ? Version : (byte == SOF1 ? Sof2 : Sof1); break;
    case Version:
      crc_ = addCrc(0xFFFF, byte);
      if (byte != VERSION) { reset(); return Result::VersionError; }
      state_ = TypeByte; break;
    case TypeByte: frame_.type = Type(byte); crc_ = addCrc(crc_, byte); state_ = Sequence; break;
    case Sequence: frame_.sequence = byte; crc_ = addCrc(crc_, byte); state_ = Length; break;
    case Length:
      frame_.length = byte; crc_ = addCrc(crc_, byte); index_ = 0;
      if (byte > MAX_PAYLOAD) { reset(); return Result::LengthError; }
      state_ = byte ? Payload : CrcLow; break;
    case Payload:
      frame_.payload[index_++] = byte; crc_ = addCrc(crc_, byte);
      if (index_ == frame_.length) state_ = CrcLow;
      break;
    case CrcLow: receivedCrc_ = byte; state_ = CrcHigh; break;
    case CrcHigh: {
      receivedCrc_ |= uint16_t(byte) << 8;
      const bool valid = receivedCrc_ == crc_;
      if (valid) frame = frame_;
      reset(); return valid ? Result::Ready : Result::CrcError;
    }
  }
  return Result::None;
}
}
