#pragma once
// Radio settings copied from the user's working HOST-01 receiver sketch.
#define LORA_SCK 18
#define LORA_MISO 19
#define LORA_MOSI 23
#define LORA_SS 5
#define LORA_RST 14
#define LORA_DIO0 26
#define LORA_HZ 433E6
#define LORA_SF 7
#define LORA_BW 125E3
#define LORA_CR 5
#define LORA_TX_POWER 17
#define LORA_SYNC_WORD 0x34
#define LORA_CRC true
// HOST-01 forwards to the PC over USB serial at 115200 baud. No Wi-Fi.
