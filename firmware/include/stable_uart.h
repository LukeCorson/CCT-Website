#pragma once
#include <Arduino.h>
namespace Uart {
constexpr uint8_t SOF1 = 0xA5, SOF2 = 0x5A, VERSION = 1, MAX_PAYLOAD = 48;
constexpr size_t MAX_FRAME = MAX_PAYLOAD + 8;
enum class Type : uint8_t { SetOutputs = 0x10, Status = 0x20, Ping = 0x30 };
enum class Result : uint8_t { None, Ready, CrcError, LengthError, VersionError };
struct Frame { Type type = Type::Ping; uint8_t sequence = 0, length = 0, payload[MAX_PAYLOAD] = {}; };
uint16_t crc16(const uint8_t *data, size_t length);
bool send(Print &port, Type type, uint8_t sequence, const uint8_t *payload, uint8_t length);
uint16_t read16(const uint8_t *data);
uint32_t read32(const uint8_t *data);
void write16(uint8_t *data, uint16_t value);
void write32(uint8_t *data, uint32_t value);
class Parser {
 public: Result push(uint8_t byte, Frame &frame);
 private:
  enum State : uint8_t { Sof1, Sof2, Version, TypeByte, Sequence, Length, Payload, CrcLow, CrcHigh };
  void reset();
  State state_ = Sof1;
  Frame frame_;
  uint8_t index_ = 0;
  uint16_t crc_ = 0xFFFF, receivedCrc_ = 0;
};
}
