#include <Arduino.h>
#include <SPI.h>
#include <LoRa.h>
#include <math.h>
#include <stdlib.h>

// ============================================================
// TERRAVEIL HOST-01 / MAIN HUB
// Collision-free polling controller + parser + ACK gateway
//
// Compatible with the unified positional packet format used by all nodes:
//
//   UG-01 / UG-02:
//   DATA,UG-01,27,-1.25,0.82,4.10,0.021,-0.018,0.998,0.140,-0.220,0.075,0,2184
//
//   LD-01:
//   DATA,LD-01,27,42.75,2184,-1.25,0.82,4.10,
//   0.021,-0.018,0.998,0.140,-0.220,0.075
//
// All packets begin with:
//   DATA,<NODE_ID>,<SEQ>,...
//
// HOST-controlled channel access:
//   POLL,<NODE_ID>
//
// Only the addressed node may transmit.
// ACK returned to every VALID, KNOWN node packet:
//   ACK,<NODE_ID>,<SEQ>
// ============================================================

// ============================================================
// LORA RA-02 -> ESP32
// ============================================================

#define LORA_SS   5
#define LORA_RST  14
#define LORA_DIO0 26

#define LORA_SCK  18
#define LORA_MISO 19
#define LORA_MOSI 23

#define LORA_FREQUENCY 433E6

// Must match all field nodes.
#define LORA_SPREADING_FACTOR 7
#define LORA_BANDWIDTH        125E3
#define LORA_CODING_RATE      5
#define LORA_SYNC_WORD        0x34
#define LORA_TX_POWER         17

// ============================================================
// HUB CONFIGURATION
// ============================================================

const unsigned long HEALTH_PRINT_INTERVAL_MS = 30000UL;
const unsigned long NODE_OFFLINE_AFTER_MS     = 15000UL;

// Fast/stable target: one complete acquisition round per second while the
// network is healthy. Only one field node is allowed to answer at a time.
const unsigned long POLLING_ROUND_INTERVAL_MS = 1000UL;
const unsigned long POLL_RESPONSE_TIMEOUT_MS  = 750UL;
const unsigned long INTER_POLL_GUARD_MS       = 25UL;
const unsigned long RETRY_GUARD_MS            = 75UL;

const size_t MAX_PACKET_LENGTH = 255;
const size_t MAX_CSV_FIELDS    = 20;

// ============================================================
// NODE / TELEMETRY TYPES
// ============================================================

enum NodeKind
{
    NODE_UNKNOWN = 0,
    NODE_UNDERGROUND,
    NODE_LINEAR_DISPLACEMENT
};

struct TelemetryData
{
    NodeKind kind;
    String nodeId;
    unsigned long sequence;

    // Underground node data
    float roll;
    float pitch;
    bool vibration;
    int soilADC;

    // Linear displacement node data
    float displacementMM;
    int potADC;
    float yaw;

    float ax;
    float ay;
    float az;

    float gx;
    float gy;
    float gz;
};

struct NodeState
{
    const char* nodeId;
    NodeKind expectedKind;

    bool seen;
    unsigned long lastSequence;
    unsigned long lastSeenMs;

    uint32_t receivedPackets;
    uint32_t duplicatePackets;

    int lastRSSI;
    float lastSNR;
};

NodeState nodeStates[] =
{
    {"UG-01", NODE_UNDERGROUND,       false, 0, 0, 0, 0, 0, 0.0f},
    {"UG-02", NODE_UNDERGROUND,       false, 0, 0, 0, 0, 0, 0.0f},
    {"LD-01", NODE_LINEAR_DISPLACEMENT, false, 0, 0, 0, 0, 0, 0.0f}
};

const size_t NODE_COUNT = sizeof(nodeStates) / sizeof(nodeStates[0]);

// ============================================================
// HUB STATISTICS
// ============================================================

uint32_t totalPacketsReceived = 0;
uint32_t validPacketsReceived = 0;
uint32_t malformedPackets     = 0;
uint32_t unknownNodePackets   = 0;
uint32_t ackPacketsSent       = 0;
uint32_t ackSendFailures      = 0;

uint32_t pollPacketsSent      = 0;
uint32_t pollSendFailures     = 0;
uint32_t pollTimeouts         = 0;
uint32_t retryPollsSent       = 0;
uint32_t retrySuccesses       = 0;
uint32_t pollingRounds        = 0;

unsigned long lastHealthPrint = 0;
unsigned long lastPollingRoundStart = 0;

// ============================================================
// HELPERS
// ============================================================

const char* nodeKindName(NodeKind kind)
{
    switch (kind)
    {
        case NODE_UNDERGROUND:
            return "UG";

        case NODE_LINEAR_DISPLACEMENT:
            return "LD";

        default:
            return "UNKNOWN";
    }
}

void clearTelemetry(TelemetryData &data)
{
    data.kind = NODE_UNKNOWN;
    data.nodeId = "";
    data.sequence = 0;

    data.roll = 0.0f;
    data.pitch = 0.0f;
    data.vibration = false;
    data.soilADC = 0;

    data.displacementMM = 0.0f;
    data.potADC = 0;
    data.yaw = 0.0f;

    data.ax = 0.0f;
    data.ay = 0.0f;
    data.az = 0.0f;

    data.gx = 0.0f;
    data.gy = 0.0f;
    data.gz = 0.0f;
}

NodeState* findNodeState(const String &nodeId)
{
    for (size_t i = 0; i < NODE_COUNT; i++)
    {
        if (nodeId == nodeStates[i].nodeId)
        {
            return &nodeStates[i];
        }
    }

    return nullptr;
}

bool parseUnsignedLongStrict(const String &input, unsigned long &value)
{
    String s = input;
    s.trim();

    if (s.length() == 0)
    {
        return false;
    }

    for (size_t i = 0; i < s.length(); i++)
    {
        if (!isDigit(s.charAt(i)))
        {
            return false;
        }
    }

    char *endPtr = nullptr;
    unsigned long parsed = strtoul(s.c_str(), &endPtr, 10);

    if (endPtr == s.c_str() || *endPtr != '\0')
    {
        return false;
    }

    value = parsed;
    return true;
}

bool parseIntStrict(const String &input, int &value)
{
    String s = input;
    s.trim();

    if (s.length() == 0)
    {
        return false;
    }

    char *endPtr = nullptr;
    long parsed = strtol(s.c_str(), &endPtr, 10);

    if (endPtr == s.c_str() || *endPtr != '\0')
    {
        return false;
    }

    value = (int) parsed;
    return true;
}

bool parseFloatStrict(const String &input, float &value)
{
    String s = input;
    s.trim();

    if (s.length() == 0)
    {
        return false;
    }

    char *endPtr = nullptr;
    float parsed = strtof(s.c_str(), &endPtr);

    if (endPtr == s.c_str() || *endPtr != '\0' || !isfinite(parsed))
    {
        return false;
    }

    value = parsed;
    return true;
}

size_t splitCSV(
    const String &packet,
    String fields[],
    size_t maxFields
)
{
    size_t count = 0;
    int start = 0;
    const int length = packet.length();

    for (int i = 0; i <= length; i++)
    {
        if (i == length || packet.charAt(i) == ',')
        {
            if (count >= maxFields)
            {
                return maxFields + 1;
            }

            fields[count] = packet.substring(start, i);
            fields[count].trim();

            count++;
            start = i + 1;
        }
    }

    return count;
}

// ============================================================
// UG PARSER
// ============================================================
// Expected positional format:
// DATA,UG-01,SEQ,ROLL,PITCH,YAW,AX,AY,AZ,GX,GY,GZ,VIBRATION,SOIL_ADC
// ============================================================

bool parseUndergroundPacket(
    String fields[],
    size_t fieldCount,
    TelemetryData &data
)
{
    // DATA + NODE + 12 payload fields = 14 CSV fields total.
    if (fieldCount != 14)
    {
        return false;
    }

    if (fields[0] != "DATA")
    {
        return false;
    }

    if (!fields[1].startsWith("UG-"))
    {
        return false;
    }

    unsigned long sequence = 0;

    float roll = 0.0f;
    float pitch = 0.0f;
    float yaw = 0.0f;

    float ax = 0.0f;
    float ay = 0.0f;
    float az = 0.0f;

    float gx = 0.0f;
    float gy = 0.0f;
    float gz = 0.0f;

    int vibration = 0;
    int soilADC = 0;

    if (!parseUnsignedLongStrict(fields[2], sequence))
    {
        return false;
    }

    if (!parseFloatStrict(fields[3], roll))
    {
        return false;
    }

    if (!parseFloatStrict(fields[4], pitch))
    {
        return false;
    }

    if (!parseFloatStrict(fields[5], yaw))
    {
        return false;
    }

    if (!parseFloatStrict(fields[6], ax))
    {
        return false;
    }

    if (!parseFloatStrict(fields[7], ay))
    {
        return false;
    }

    if (!parseFloatStrict(fields[8], az))
    {
        return false;
    }

    if (!parseFloatStrict(fields[9], gx))
    {
        return false;
    }

    if (!parseFloatStrict(fields[10], gy))
    {
        return false;
    }

    if (!parseFloatStrict(fields[11], gz))
    {
        return false;
    }

    if (!parseIntStrict(fields[12], vibration))
    {
        return false;
    }

    if (vibration != 0 && vibration != 1)
    {
        return false;
    }

    if (!parseIntStrict(fields[13], soilADC))
    {
        return false;
    }

    clearTelemetry(data);

    data.kind = NODE_UNDERGROUND;
    data.nodeId = fields[1];
    data.sequence = sequence;

    data.roll = roll;
    data.pitch = pitch;
    data.yaw = yaw;

    data.ax = ax;
    data.ay = ay;
    data.az = az;

    data.gx = gx;
    data.gy = gy;
    data.gz = gz;

    data.vibration = (vibration == 1);
    data.soilADC = soilADC;

    return true;
}

// ============================================================
// LD PARSER
// ============================================================
// Expected positional format:
// DATA,LD-01,SEQ,DISP_MM,POT_ADC,ROLL,PITCH,YAW,
// AX,AY,AZ,GX,GY,GZ
// ============================================================

bool parseLinearDisplacementPacket(
    String fields[],
    size_t fieldCount,
    TelemetryData &data
)
{
    // DATA + NODE + 12 payload fields = 14 CSV fields total.
    if (fieldCount != 14)
    {
        return false;
    }

    if (fields[0] != "DATA")
    {
        return false;
    }

    if (!fields[1].startsWith("LD-"))
    {
        return false;
    }

    unsigned long sequence = 0;

    float displacementMM = 0.0f;
    int potADC = 0;

    float roll = 0.0f;
    float pitch = 0.0f;
    float yaw = 0.0f;

    float ax = 0.0f;
    float ay = 0.0f;
    float az = 0.0f;

    float gx = 0.0f;
    float gy = 0.0f;
    float gz = 0.0f;

    if (!parseUnsignedLongStrict(fields[2], sequence))
    {
        return false;
    }

    if (!parseFloatStrict(fields[3], displacementMM))
    {
        return false;
    }

    if (!parseIntStrict(fields[4], potADC))
    {
        return false;
    }

    if (!parseFloatStrict(fields[5], roll))
    {
        return false;
    }

    if (!parseFloatStrict(fields[6], pitch))
    {
        return false;
    }

    if (!parseFloatStrict(fields[7], yaw))
    {
        return false;
    }

    if (!parseFloatStrict(fields[8], ax))
    {
        return false;
    }

    if (!parseFloatStrict(fields[9], ay))
    {
        return false;
    }

    if (!parseFloatStrict(fields[10], az))
    {
        return false;
    }

    if (!parseFloatStrict(fields[11], gx))
    {
        return false;
    }

    if (!parseFloatStrict(fields[12], gy))
    {
        return false;
    }

    if (!parseFloatStrict(fields[13], gz))
    {
        return false;
    }

    clearTelemetry(data);

    data.kind = NODE_LINEAR_DISPLACEMENT;
    data.nodeId = fields[1];
    data.sequence = sequence;

    data.displacementMM = displacementMM;
    data.potADC = potADC;

    data.roll = roll;
    data.pitch = pitch;
    data.yaw = yaw;

    data.ax = ax;
    data.ay = ay;
    data.az = az;

    data.gx = gx;
    data.gy = gy;
    data.gz = gz;

    return true;
}

// ============================================================
// MASTER PARSER
// ============================================================
// Every TeraVeil telemetry packet now begins:
//   DATA,<NODE_ID>,<SEQ>,...
//
// NODE_ID decides which payload schema must be used.
// ============================================================

bool parseTelemetryPacket(
    const String &packet,
    TelemetryData &data
)
{
    String fields[MAX_CSV_FIELDS];

    size_t fieldCount =
        splitCSV(
            packet,
            fields,
            MAX_CSV_FIELDS
        );

    if (fieldCount == 0 || fieldCount > MAX_CSV_FIELDS)
    {
        return false;
    }

    if (fieldCount < 3)
    {
        return false;
    }

    if (fields[0] != "DATA")
    {
        return false;
    }

    const String &nodeId = fields[1];

    if (nodeId.startsWith("UG-"))
    {
        return parseUndergroundPacket(
            fields,
            fieldCount,
            data
        );
    }

    if (nodeId.startsWith("LD-"))
    {
        return parseLinearDisplacementPacket(
            fields,
            fieldCount,
            data
        );
    }

    return false;
}

// ============================================================
// ACK TRANSMITTER
// ============================================================

bool sendAck(
    const String &nodeId,
    unsigned long sequence
)
{
    String ack =
        "ACK," +
        nodeId +
        "," +
        String(sequence);

    // Stop receive mode before transmitting.
    LoRa.idle();

    LoRa.beginPacket();
    LoRa.print(ack);

    int result = LoRa.endPacket();

    // Immediately return HOST-01 to continuous RX mode.
    LoRa.receive();

    if (result == 1)
    {
        ackPacketsSent++;

        Serial.print("[ACK TX] ");
        Serial.println(ack);

        return true;
    }

    ackSendFailures++;

    Serial.print("[ACK TX FAILED] ");
    Serial.println(ack);

    return false;
}

// ============================================================
// NORMALIZED SERIAL OUTPUT
// ============================================================
// This gives the PC/web-app side one predictable key/value stream
// while the radio side stays compact and positional.
// ============================================================

void printNormalizedTelemetry(
    const TelemetryData &data,
    int rssi,
    float snr
)
{
    Serial.print("HUB_DATA,");

    Serial.print("NODE=");
    Serial.print(data.nodeId);

    Serial.print(",TYPE=");
    Serial.print(nodeKindName(data.kind));

    Serial.print(",SEQ=");
    Serial.print(data.sequence);

    if (data.kind == NODE_UNDERGROUND)
    {
        Serial.print(",ROLL=");
        Serial.print(data.roll, 2);

        Serial.print(",PITCH=");
        Serial.print(data.pitch, 2);

        Serial.print(",YAW=");
        Serial.print(data.yaw, 2);

        Serial.print(",AX=");
        Serial.print(data.ax, 3);

        Serial.print(",AY=");
        Serial.print(data.ay, 3);

        Serial.print(",AZ=");
        Serial.print(data.az, 3);

        Serial.print(",GX=");
        Serial.print(data.gx, 3);

        Serial.print(",GY=");
        Serial.print(data.gy, 3);

        Serial.print(",GZ=");
        Serial.print(data.gz, 3);

        Serial.print(",VIBRATION=");
        Serial.print(data.vibration ? 1 : 0);

        Serial.print(",SOIL_ADC=");
        Serial.print(data.soilADC);
    }
    else if (data.kind == NODE_LINEAR_DISPLACEMENT)
    {
        Serial.print(",DISP_MM=");
        Serial.print(data.displacementMM, 2);

        Serial.print(",POT_ADC=");
        Serial.print(data.potADC);

        Serial.print(",ROLL=");
        Serial.print(data.roll, 2);

        Serial.print(",PITCH=");
        Serial.print(data.pitch, 2);

        Serial.print(",YAW=");
        Serial.print(data.yaw, 2);

        Serial.print(",AX=");
        Serial.print(data.ax, 3);

        Serial.print(",AY=");
        Serial.print(data.ay, 3);

        Serial.print(",AZ=");
        Serial.print(data.az, 3);

        Serial.print(",GX=");
        Serial.print(data.gx, 3);

        Serial.print(",GY=");
        Serial.print(data.gy, 3);

        Serial.print(",GZ=");
        Serial.print(data.gz, 3);
    }

    Serial.print(",RSSI=");
    Serial.print(rssi);

    Serial.print(",SNR=");
    Serial.println(snr, 2);
}

// ============================================================
// HUMAN-READABLE DISPLAY
// ============================================================

void printTelemetryBlock(
    const TelemetryData &data,
    int rssi,
    float snr,
    bool duplicate
)
{
    Serial.println();
    Serial.println("==================================================");
    Serial.println("             TERRAVEIL HUB RX");
    Serial.println("==================================================");

    Serial.print("Node ID       : ");
    Serial.println(data.nodeId);

    Serial.print("Node Type     : ");
    Serial.println(nodeKindName(data.kind));

    Serial.print("Sequence      : ");
    Serial.println(data.sequence);

    Serial.print("Packet Status : ");
    Serial.println(duplicate ? "DUPLICATE / RE-TRANSMISSION" : "NEW");

    Serial.print("RSSI          : ");
    Serial.print(rssi);
    Serial.println(" dBm");

    Serial.print("SNR           : ");
    Serial.print(snr, 2);
    Serial.println(" dB");

    if (data.kind == NODE_UNDERGROUND)
    {
        Serial.println("---------------- UNDERGROUND DATA ----------------");

        Serial.print("Roll          : ");
        Serial.print(data.roll, 2);
        Serial.println(" deg");

        Serial.print("Pitch         : ");
        Serial.print(data.pitch, 2);
        Serial.println(" deg");

        Serial.print("Yaw           : ");
        Serial.print(data.yaw, 2);
        Serial.println(" deg (relative)");

        Serial.print("Accel XYZ     : ");
        Serial.print(data.ax, 3);
        Serial.print(", ");
        Serial.print(data.ay, 3);
        Serial.print(", ");
        Serial.print(data.az, 3);
        Serial.println(" g");

        Serial.print("Gyro XYZ      : ");
        Serial.print(data.gx, 3);
        Serial.print(", ");
        Serial.print(data.gy, 3);
        Serial.print(", ");
        Serial.print(data.gz, 3);
        Serial.println(" deg/s");

        Serial.print("Vibration     : ");
        Serial.println(data.vibration ? "DETECTED" : "NORMAL");

        Serial.print("Soil ADC      : ");
        Serial.println(data.soilADC);
    }
    else if (data.kind == NODE_LINEAR_DISPLACEMENT)
    {
        Serial.println("------------- LINEAR DISPLACEMENT DATA ------------");

        Serial.print("Displacement  : ");
        Serial.print(data.displacementMM, 2);
        Serial.println(" mm");

        Serial.print("Pot ADC       : ");
        Serial.println(data.potADC);

        Serial.print("Roll          : ");
        Serial.print(data.roll, 2);
        Serial.println(" deg");

        Serial.print("Pitch         : ");
        Serial.print(data.pitch, 2);
        Serial.println(" deg");

        Serial.print("Yaw           : ");
        Serial.print(data.yaw, 2);
        Serial.println(" deg (relative)");

        Serial.print("Accel XYZ     : ");
        Serial.print(data.ax, 3);
        Serial.print(", ");
        Serial.print(data.ay, 3);
        Serial.print(", ");
        Serial.print(data.az, 3);
        Serial.println(" g");

        Serial.print("Gyro XYZ      : ");
        Serial.print(data.gx, 3);
        Serial.print(", ");
        Serial.print(data.gy, 3);
        Serial.print(", ");
        Serial.print(data.gz, 3);
        Serial.println(" deg/s");
    }

    Serial.println("==================================================");
}

// ============================================================
// NODE STATE UPDATE
// ============================================================

bool updateNodeState(
    NodeState &state,
    const TelemetryData &data,
    int rssi,
    float snr
)
{
    bool duplicate =
        state.seen &&
        data.sequence == state.lastSequence;

    if (duplicate)
    {
        state.duplicatePackets++;
    }
    else
    {
        state.receivedPackets++;
    }

    state.seen = true;
    state.lastSequence = data.sequence;
    state.lastSeenMs = millis();
    state.lastRSSI = rssi;
    state.lastSNR = snr;

    return duplicate;
}

// ============================================================
// PERIODIC NETWORK HEALTH
// ============================================================

void printNetworkHealth()
{
    unsigned long now = millis();

    if (
        now - lastHealthPrint <
        HEALTH_PRINT_INTERVAL_MS
    )
    {
        return;
    }

    lastHealthPrint = now;

    Serial.println();
    Serial.println("################ HUB NETWORK HEALTH ################");

    for (size_t i = 0; i < NODE_COUNT; i++)
    {
        NodeState &state = nodeStates[i];

        Serial.print(state.nodeId);
        Serial.print(" | ");

        if (!state.seen)
        {
            Serial.println("NOT SEEN YET");
            continue;
        }

        unsigned long age = now - state.lastSeenMs;
        bool online = age <= NODE_OFFLINE_AFTER_MS;

        Serial.print(online ? "ONLINE" : "OFFLINE/STALE");
        Serial.print(" | last seq=");
        Serial.print(state.lastSequence);
        Serial.print(" | age=");
        Serial.print(age);
        Serial.print(" ms | rx=");
        Serial.print(state.receivedPackets);
        Serial.print(" | dup=");
        Serial.print(state.duplicatePackets);
        Serial.print(" | RSSI=");
        Serial.print(state.lastRSSI);
        Serial.print(" | SNR=");
        Serial.println(state.lastSNR, 2);
    }

    Serial.println("----------------------------------------------------");
    Serial.print("Total radio packets : ");
    Serial.println(totalPacketsReceived);

    Serial.print("Valid packets       : ");
    Serial.println(validPacketsReceived);

    Serial.print("Malformed packets   : ");
    Serial.println(malformedPackets);

    Serial.print("Unknown nodes       : ");
    Serial.println(unknownNodePackets);

    Serial.print("ACK sent            : ");
    Serial.println(ackPacketsSent);

    Serial.print("ACK failures        : ");
    Serial.println(ackSendFailures);

    Serial.print("Polling rounds      : ");
    Serial.println(pollingRounds);

    Serial.print("Poll packets sent   : ");
    Serial.println(pollPacketsSent);

    Serial.print("Poll TX failures    : ");
    Serial.println(pollSendFailures);

    Serial.print("Poll timeouts       : ");
    Serial.println(pollTimeouts);

    Serial.print("Deferred retries    : ");
    Serial.println(retryPollsSent);

    Serial.print("Retry successes     : ");
    Serial.println(retrySuccesses);

    Serial.println("####################################################");
    Serial.println();
}

// ============================================================
// PACKET HANDLER
// ============================================================

void handleIncomingPacket(int packetSize)
{
    totalPacketsReceived++;

    // Capture RF metrics BEFORE transmitting the ACK.
    int rssi = LoRa.packetRssi();
    float snr = LoRa.packetSnr();

    String packet = "";
    packet.reserve(MAX_PACKET_LENGTH);

    bool overflow = false;

    while (LoRa.available())
    {
        char c = (char) LoRa.read();

        if (packet.length() < MAX_PACKET_LENGTH)
        {
            packet += c;
        }
        else
        {
            overflow = true;
        }
    }

    packet.trim();

    Serial.println();
    Serial.print("[LoRa RX RAW] ");
    Serial.println(packet);

    Serial.print("[LoRa RX RF ] size=");
    Serial.print(packetSize);
    Serial.print(" RSSI=");
    Serial.print(rssi);
    Serial.print(" SNR=");
    Serial.println(snr, 2);

    if (overflow || packet.length() == 0)
    {
        malformedPackets++;

        Serial.println("[PARSE ERROR] Empty or oversized packet.");
        LoRa.receive();
        return;
    }

    TelemetryData data;
    clearTelemetry(data);

    if (!parseTelemetryPacket(packet, data))
    {
        malformedPackets++;

        Serial.println("[PARSE ERROR] Packet format not recognized.");
        LoRa.receive();
        return;
    }

    NodeState* state = findNodeState(data.nodeId);

    if (state == nullptr)
    {
        unknownNodePackets++;

        Serial.print("[NODE REJECTED] Unknown NODE_ID: ");
        Serial.println(data.nodeId);

        // Do not ACK unknown nodes.
        LoRa.receive();
        return;
    }

    if (state->expectedKind != data.kind)
    {
        malformedPackets++;

        Serial.print("[TYPE ERROR] NODE_ID/type mismatch for ");
        Serial.println(data.nodeId);

        LoRa.receive();
        return;
    }

    validPacketsReceived++;

    // --------------------------------------------------------
    // ACK FIRST.
    // The field nodes wait only 600 ms, so acknowledge the
    // validated packet before doing verbose serial printing.
    // --------------------------------------------------------

    bool ackOK = sendAck(
        data.nodeId,
        data.sequence
    );

    if (!ackOK)
    {
        Serial.println("[WARNING] Valid telemetry received, but ACK TX failed.");
    }

    bool duplicate = updateNodeState(
        *state,
        data,
        rssi,
        snr
    );

    // Normalized line for a PC / web-app bridge.
    printNormalizedTelemetry(
        data,
        rssi,
        snr
    );

    // Human-readable diagnostic block.
    printTelemetryBlock(
        data,
        rssi,
        snr,
        duplicate
    );
}

// ============================================================
// POLLING CONTROLLER
// ============================================================
// HOST-01 is the only device that decides who may use the channel.
// A field node transmits only after receiving POLL,<its NODE_ID>.
// ============================================================

bool sendPoll(
    const char* nodeId,
    bool isRetry
)
{
    String poll =
        "POLL," + String(nodeId);

    LoRa.idle();

    LoRa.beginPacket();
    LoRa.print(poll);

    int result =
        LoRa.endPacket();

    // Be ready for the node's immediate response.
    LoRa.receive();

    if (result != 1)
    {
        pollSendFailures++;

        Serial.print("[POLL TX FAILED] ");
        Serial.println(poll);

        return false;
    }

    pollPacketsSent++;

    if (isRetry)
    {
        retryPollsSent++;
    }

    Serial.print(isRetry ? "[POLL RETRY TX] " : "[POLL TX] ");
    Serial.println(poll);

    return true;
}


bool pollNode(
    NodeState &target,
    bool isRetry
)
{
    // Snapshot the target's packet counter. handleIncomingPacket()
    // increments either receivedPackets or duplicatePackets for every
    // valid response from this node.
    uint32_t beforeCount =
        target.receivedPackets +
        target.duplicatePackets;

    if (!sendPoll(target.nodeId, isRetry))
    {
        return false;
    }

    unsigned long waitStart =
        millis();

    while (
        millis() - waitStart <
        POLL_RESPONSE_TIMEOUT_MS
    )
    {
        int packetSize =
            LoRa.parsePacket();

        if (packetSize > 0)
        {
            // This validates, ACKs, prints and updates NodeState.
            // If an unexpected known node somehow talks, it is handled,
            // but the hub continues waiting for the node it actually polled.
            handleIncomingPacket(packetSize);

            uint32_t afterCount =
                target.receivedPackets +
                target.duplicatePackets;

            if (afterCount > beforeCount)
            {
                Serial.print("[POLL OK] ");
                Serial.println(target.nodeId);

                if (isRetry)
                {
                    retrySuccesses++;
                }

                return true;
            }
        }

        delay(1);
    }

    pollTimeouts++;

    Serial.print("[POLL TIMEOUT] ");
    Serial.println(target.nodeId);

    // Keep receiver armed for the next transaction.
    LoRa.receive();

    return false;
}


void runPollingRound()
{
    pollingRounds++;

    Serial.println();
    Serial.println("==================================================");
    Serial.print("        POLLING ROUND #");
    Serial.println(pollingRounds);
    Serial.println("==================================================");

    bool missed[NODE_COUNT] = {};
    bool anyMissed = false;

    // --------------------------------------------------------
    // FIRST PASS: every node gets exactly one turn.
    // --------------------------------------------------------

    for (size_t i = 0; i < NODE_COUNT; i++)
    {
        bool success =
            pollNode(
                nodeStates[i],
                false
            );

        missed[i] = !success;

        if (!success)
        {
            anyMissed = true;
        }

        delay(INTER_POLL_GUARD_MS);
    }

    // --------------------------------------------------------
    // DEFERRED RETRY: do not let one weak/dead node block the
    // rest of the network. Complete the whole first pass, then
    // retry only the nodes that were missed.
    // --------------------------------------------------------

    if (anyMissed)
    {
        Serial.println();
        Serial.println("[POLL] Starting deferred retry pass...");

        delay(RETRY_GUARD_MS);

        for (size_t i = 0; i < NODE_COUNT; i++)
        {
            if (!missed[i])
            {
                continue;
            }

            pollNode(
                nodeStates[i],
                true
            );

            delay(INTER_POLL_GUARD_MS);
        }
    }

    Serial.println("[POLL] Round complete.");
    Serial.println();
}


// ============================================================
// SETUP
// ============================================================

void setup()
{
    Serial.begin(115200);
    delay(1000);

    Serial.println();
    Serial.println("==================================================");
    Serial.println("        TERRAVEIL HOST-01 / MAIN HUB");
    Serial.println("   Collision-Free Polling LoRa Gateway");
    Serial.println("==================================================");

    Serial.println("Registered nodes:");

    for (size_t i = 0; i < NODE_COUNT; i++)
    {
        Serial.print("  - ");
        Serial.print(nodeStates[i].nodeId);
        Serial.print(" [");
        Serial.print(nodeKindName(nodeStates[i].expectedKind));
        Serial.println("]");
    }

    Serial.println();
    Serial.println("Starting SPI + LoRa...");

    SPI.begin(
        LORA_SCK,
        LORA_MISO,
        LORA_MOSI,
        LORA_SS
    );

    LoRa.setPins(
        LORA_SS,
        LORA_RST,
        LORA_DIO0
    );

    if (!LoRa.begin(LORA_FREQUENCY))
    {
        Serial.println("FATAL: LoRa initialization failed.");

        while (true)
        {
            delay(1000);
        }
    }

    // Must match UG-01, UG-02 and LD-01 exactly.
    LoRa.setSpreadingFactor(LORA_SPREADING_FACTOR);
    LoRa.setSignalBandwidth(LORA_BANDWIDTH);
    LoRa.setCodingRate4(LORA_CODING_RATE);
    LoRa.setSyncWord(LORA_SYNC_WORD);
    LoRa.enableCrc();
    LoRa.setTxPower(LORA_TX_POWER);

    // Continuous receive mode.
    LoRa.receive();

    Serial.println("LoRa initialized successfully.");
    Serial.println("Frequency   : 433 MHz");
    Serial.println("SF          : 7");
    Serial.println("Bandwidth   : 125 kHz");
    Serial.println("Coding Rate : 4/5");
    Serial.println("Sync Word   : 0x34");
    Serial.println("CRC         : Enabled");
    Serial.println();
    Serial.println("HOST-01 READY - polling UG-01, UG-02, LD-01");
    Serial.println("==================================================");
    Serial.println();
}

// ============================================================
// LOOP
// ============================================================

void loop()
{
    unsigned long now =
        millis();

    // Start immediately on boot, then approximately every 1 second.
    if (
        lastPollingRoundStart == 0
        ||
        now - lastPollingRoundStart >=
        POLLING_ROUND_INTERVAL_MS
    )
    {
        lastPollingRoundStart = now;

        runPollingRound();

        // If a badly degraded network made the round itself take longer
        // than the configured interval, do not immediately hammer the
        // nodes with another round. Restart the interval from here.
        if (
            millis() - lastPollingRoundStart >=
            POLLING_ROUND_INTERVAL_MS
        )
        {
            lastPollingRoundStart =
                millis();
        }
    }

    // Normally there should be no unsolicited DATA packets because all
    // field nodes are polling-controlled. Keeping this listener makes the
    // hub tolerant of a late packet during debugging or migration.
    int packetSize =
        LoRa.parsePacket();

    if (packetSize > 0)
    {
        handleIncomingPacket(packetSize);
    }

    printNetworkHealth();

    delay(1);
}
