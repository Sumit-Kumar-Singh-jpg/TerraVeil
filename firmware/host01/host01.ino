#include <Arduino.h>
#include <SPI.h>
#include <LoRa.h>
#include <ArduinoJson.h>
#include <esp_timer.h>
#include <math.h>
#include <errno.h>
#if __has_include("host_config.h")
#include "host_config.h"
#else
#include "config.example.h"
#endif

// Radio ownership is exclusive to loop(). USB never holds a radio lock.
struct Packet {
  uint32_t sequence;
  float roll, pitch, vibration, soil, snr;
  int rssi;
  int64_t receivedUs;
};
constexpr size_t CAPACITY = 256;
Packet pending[CAPACITY];
size_t head = 0, count = 0;
uint32_t overflowCount = 0;
portMUX_TYPE queueMux = portMUX_INITIALIZER_UNLOCKED;
bool transmitting = false;
volatile bool ackDone = false;
void onAckDone() { ackDone = true; }

bool numeric(const char* text, float& value, float low, float high) {
  if (!text[0] || isspace((unsigned char)text[0])) return false;
  char* end;
  errno = 0;
  value = strtof(text, &end);
  return end != text && *end == '\0' && errno != ERANGE && isfinite(value) && value >= low && value <= high;
}

bool parse(char* text, Packet& packet) {
  char* fields[7]; size_t n = 1; fields[0] = text;
  for (char* p = text; *p; ++p) if (*p == ',') {
    if (n == 7) return false;
    *p = '\0'; fields[n++] = p + 1;
  }
  if (n != 7 || strcmp(fields[0], "DATA") || strcmp(fields[1], "UG-01")) return false;
  if (!*fields[2]) return false;
  for (char* p = fields[2]; *p; ++p) if (*p < '0' || *p > '9') return false;
  errno = 0; char* end;
  unsigned long long sequence = strtoull(fields[2], &end, 10);
  if (*end || errno == ERANGE || sequence > UINT32_MAX) return false;
  packet.sequence = (uint32_t)sequence;
  return numeric(fields[3], packet.roll, -180, 180) && numeric(fields[4], packet.pitch, -180, 180)
      && numeric(fields[5], packet.vibration, 0, 1) && (packet.vibration == 0 || packet.vibration == 1)
      && numeric(fields[6], packet.soil, 0, 4095);
}

void enqueue(const Packet& packet) {
  bool overflow = false;
  portENTER_CRITICAL(&queueMux);
  // A retry is ACKed again but does not replace the original capture time.
  for (size_t i = 0; i < count; ++i) if (pending[(head+i)%CAPACITY].sequence == packet.sequence) {
    portEXIT_CRITICAL(&queueMux); return;
  }
  // Preserve the oldest queued packet, including the one being uploaded.
  if (count == CAPACITY) { ++overflowCount; overflow = true; }
  else { pending[(head+count)%CAPACITY] = packet; ++count; }
  portEXIT_CRITICAL(&queueMux);
  if (overflow) Serial.printf("BUFFER FULL: dropped newest seq=%lu; total=%lu\n", (unsigned long)packet.sequence, (unsigned long)overflowCount);
}

// Mother Host replies only after SQLite commits: STORED,UG-01,<sequence>.
// No Wi-Fi is used. A lost USB confirmation safely causes a retransmission.
void usbForwarder(void*) {
  char input[64]; size_t used=0; bool discard=false;
  int64_t nextSend=0;
  for (;;) {
    while (Serial.available()) {
      char c=(char)Serial.read();
      if (c=='\n') {
        input[used]='\0';
        const char* prefix="STORED,UG-01,";
        if (!discard && strncmp(input,prefix,strlen(prefix))==0) {
          char* end; errno=0;
          const char* value=input+strlen(prefix);
          unsigned long sequence=strtoul(value,&end,10);
          if (*value && end!=value && *end=='\0' && errno!=ERANGE) {
            portENTER_CRITICAL(&queueMux);
            if(count && pending[head].sequence==sequence) {head=(head+1)%CAPACITY; --count;nextSend=0;}
            portEXIT_CRITICAL(&queueMux);
          }
        }
        used=0;discard=false;
      } else if(c!='\r') {
        if(used<sizeof(input)-1 && !discard) input[used++]=c;
        else discard=true;
      }
    }
    Packet packet; bool available;
    portENTER_CRITICAL(&queueMux);
    available=count>0;
    if(available) packet=pending[head];
    portEXIT_CRITICAL(&queueMux);
    int64_t now=esp_timer_get_time();
    if(available && now>=nextSend) {
      JsonDocument doc;
      doc["host_id"]="HOST-01";doc["zone_id"]="ZONE-A";doc["node_id"]="UG-01";
      doc["sequence"]=packet.sequence;doc["roll"]=packet.roll;doc["pitch"]=packet.pitch;
      doc["vibration"]=packet.vibration;doc["soil"]=packet.soil;
      doc["rssi"]=packet.rssi;doc["snr"]=packet.snr;
      doc["queue_age_ms"]=(now-packet.receivedUs)/1000;
      String line="TELEMETRY ";serializeJson(doc,line);line+='\n';
      Serial.write((const uint8_t*)line.c_str(),line.length());
      nextSend=now+1000000;
    }
    vTaskDelay(pdMS_TO_TICKS(10));
  }
}

void setup() {
  Serial.begin(115200);
  SPI.begin(LORA_SCK,LORA_MISO,LORA_MOSI,LORA_SS);
  LoRa.setPins(LORA_SS,LORA_RST,LORA_DIO0);
  if (!LoRa.begin(LORA_HZ)) {Serial.println("LoRa initialization failed"); while(true) delay(1000);}
  LoRa.setTxPower(LORA_TX_POWER);
  LoRa.setSpreadingFactor(LORA_SF); LoRa.setSignalBandwidth(LORA_BW);
  LoRa.setCodingRate4(LORA_CR); LoRa.setSyncWord(LORA_SYNC_WORD);
  if (LORA_CRC) LoRa.enableCrc(); else LoRa.disableCrc();
  LoRa.onTxDone(onAckDone);
  LoRa.receive();
  if (xTaskCreatePinnedToCore(usbForwarder,"usb-forward",8192,nullptr,1,nullptr,0) != pdPASS)
    Serial.println("USB task failed; radio continues with bounded buffering");
  Serial.println("HOST-01 ready; USB serial 115200; DATA/ACK protocol unchanged");
}

void loop() {
  if (transmitting) {
    if (ackDone) {transmitting=false; ackDone=false; LoRa.receive();}
    delay(1); return;
  }
  int length=LoRa.parsePacket();
  if (!length) {delay(1); return;}
  char text[160]; size_t n=0;
  while (LoRa.available()) {char c=LoRa.read(); if (n < sizeof(text)-1) text[n++]=c;}
  text[n]='\0';
  for(size_t i=0;i<n;++i) if(text[i] < 32 || text[i] > 126) {Serial.println("Rejected non-text DATA");LoRa.receive();return;}
  Packet packet{};
  if (length >= (int)sizeof(text) || !parse(text,packet)) {Serial.println("Rejected malformed DATA"); LoRa.receive(); return;}
  packet.rssi=LoRa.packetRssi(); packet.snr=LoRa.packetSnr(); packet.receivedUs=esp_timer_get_time();
  // Begin ACK transmission before any queue work. The radio is half duplex only
  // during its own ACK airtime; USB latency never stops reception.
  ackDone=false; transmitting=true;
  LoRa.beginPacket(); LoRa.print("ACK,UG-01,"); LoRa.print(packet.sequence); LoRa.endPacket(true);
  enqueue(packet);
  Serial.printf("ACK UG-01 seq=%lu RSSI=%d SNR=%.2f\n", (unsigned long)packet.sequence,packet.rssi,packet.snr);
}
