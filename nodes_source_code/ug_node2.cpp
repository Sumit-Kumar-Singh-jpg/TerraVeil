#include <Arduino.h>
#include <Wire.h>
#include <SPI.h>
#include <LoRa.h>

// ============================================================
// TERRAVEIL UG-02
// Ground Monitoring Node
// ============================================================

// -------------------------
// Node configuration
// -------------------------
const char* NODE_ID = "UG-02";

// -------------------------
// MPU6050
// -------------------------
#define MPU_ADDR 0x68

#define SDA_PIN 21
#define SCL_PIN 22

// -------------------------
// Sensors
// -------------------------
#define VIBRATION_PIN 34
#define SOIL_PIN      32
#define BUZZER_PIN    25

// -------------------------
// LoRa RA-02
// -------------------------
#define LORA_SS   5
#define LORA_RST  14
#define LORA_DIO0 26

#define LORA_FREQUENCY 433E6

// SPI pins
#define LORA_SCK  18
#define LORA_MISO 19
#define LORA_MOSI 23

// -------------------------
// Polling / ACK timing
// -------------------------
// HOST-01 owns the channel. This node transmits ONLY after receiving
// POLL,UG-02. This removes same-channel packet collisions between nodes.
const unsigned long ACK_TIMEOUT_MS = 1500;
const unsigned long POLL_TURNAROUND_MS = 20;

// -------------------------
// MPU6050 calibration
// -------------------------
const int CALIBRATION_SAMPLES = 500;

float rollOffset = 0.0;
float pitchOffset = 0.0;
float gyroZBias = 0.0;

// -------------------------
// State
// -------------------------
unsigned long sequenceNumber = 0;

// Complementary filter
float filteredRoll = 0.0;
float filteredPitch = 0.0;

// Relative yaw only; MPU6050 has no magnetometer.
float filteredYaw = 0.0;

unsigned long lastIMUTime = 0;


// ============================================================
// IMU DATA STRUCTURE
// ============================================================

struct IMUData
{
    // Accelerometer in g
    float ax;
    float ay;
    float az;

    // Gyroscope in deg/sec
    float gx;
    float gy;
    float gz;

    // Orientation in degrees
    float roll;
    float pitch;
    float yaw;
};


// ============================================================
// MPU6050 LOW LEVEL FUNCTIONS
// ============================================================

void mpuWrite(byte reg, byte data)
{
    Wire.beginTransmission(MPU_ADDR);
    Wire.write(reg);
    Wire.write(data);
    Wire.endTransmission();
}


bool mpuReadRaw(
    int16_t &ax,
    int16_t &ay,
    int16_t &az,
    int16_t &gx,
    int16_t &gy,
    int16_t &gz
)
{
    Wire.beginTransmission(MPU_ADDR);
    Wire.write(0x3B);

    if (Wire.endTransmission(false) != 0)
    {
        return false;
    }

    Wire.requestFrom(MPU_ADDR, 14, true);

    if (Wire.available() < 14)
    {
        return false;
    }

    ax = (Wire.read() << 8) | Wire.read();
    ay = (Wire.read() << 8) | Wire.read();
    az = (Wire.read() << 8) | Wire.read();

    // Temperature — ignore
    Wire.read();
    Wire.read();

    gx = (Wire.read() << 8) | Wire.read();
    gy = (Wire.read() << 8) | Wire.read();
    gz = (Wire.read() << 8) | Wire.read();

    return true;
}


bool readIMU(
    IMUData &imu
)
{
    int16_t axRaw, ayRaw, azRaw;
    int16_t gxRaw, gyRaw, gzRaw;

    if (!mpuReadRaw(
        axRaw,
        ayRaw,
        azRaw,
        gxRaw,
        gyRaw,
        gzRaw
    ))
    {
        return false;
    }

    // Convert accelerometer to g
    imu.ax = axRaw / 16384.0f;
    imu.ay = ayRaw / 16384.0f;
    imu.az = azRaw / 16384.0f;

    // Convert gyroscope to degrees/sec
    imu.gx = gxRaw / 131.0f;
    imu.gy = gyRaw / 131.0f;
    imu.gz = (gzRaw / 131.0f) - gyroZBias;

    // Accelerometer angles
    float accelRoll =
        atan2(imu.ay, imu.az) * 180.0f / PI;

    float accelPitch =
        atan2(
            -imu.ax,
            sqrt(imu.ay * imu.ay + imu.az * imu.az)
        ) * 180.0f / PI;

    // First read
    if (lastIMUTime == 0)
    {
        filteredRoll = accelRoll;
        filteredPitch = accelPitch;
        filteredYaw = 0.0f;

        lastIMUTime = millis();

        imu.roll = filteredRoll - rollOffset;
        imu.pitch = filteredPitch - pitchOffset;
        imu.yaw = filteredYaw;

        return true;
    }

    unsigned long now = millis();

    float dt =
        (now - lastIMUTime) / 1000.0f;

    lastIMUTime = now;

    // Protect against unreasonable dt
    if (dt <= 0.0f || dt > 1.0f)
    {
        dt = 0.01f;
    }

    // Gyro integration
    float gyroRoll =
        filteredRoll + imu.gx * dt;

    float gyroPitch =
        filteredPitch + imu.gy * dt;

    filteredYaw += imu.gz * dt;

    // Complementary filter
    const float alpha = 0.98f;

    filteredRoll =
        alpha * gyroRoll +
        (1.0f - alpha) * accelRoll;

    filteredPitch =
        alpha * gyroPitch +
        (1.0f - alpha) * accelPitch;

    // Keep relative yaw bounded for cleaner telemetry.
    if (filteredYaw > 180.0f)
    {
        filteredYaw -= 360.0f;
    }
    else if (filteredYaw < -180.0f)
    {
        filteredYaw += 360.0f;
    }

    imu.roll = filteredRoll - rollOffset;
    imu.pitch = filteredPitch - pitchOffset;
    imu.yaw = filteredYaw;

    return true;
}


// ============================================================
// MPU CALIBRATION
// ============================================================

void calibrateMPU()
{
    Serial.println();
    Serial.println("================================");
    Serial.println("MPU6050 CALIBRATION");
    Serial.println("Keep the node completely still.");
    Serial.println("================================");

    delay(2000);

    float rollSum = 0.0;
    float pitchSum = 0.0;
    float gyroZSum = 0.0;

    int successfulSamples = 0;

    for (int i = 0; i < CALIBRATION_SAMPLES; i++)
    {
        int16_t axRaw, ayRaw, azRaw;
        int16_t gxRaw, gyRaw, gzRaw;

        if (mpuReadRaw(
            axRaw,
            ayRaw,
            azRaw,
            gxRaw,
            gyRaw,
            gzRaw
        ))
        {
            float ax = axRaw / 16384.0;
            float ay = ayRaw / 16384.0;
            float az = azRaw / 16384.0;

            float roll =
                atan2(ay, az) * 180.0 / PI;

            float pitch =
                atan2(
                    -ax,
                    sqrt(ay * ay + az * az)
                ) * 180.0 / PI;

            float gz = gzRaw / 131.0f;

            rollSum += roll;
            pitchSum += pitch;
            gyroZSum += gz;

            successfulSamples++;
        }

        delay(4);
    }

    if (successfulSamples > 0)
    {
        rollOffset =
            rollSum / successfulSamples;

        pitchOffset =
            pitchSum / successfulSamples;

        gyroZBias =
            gyroZSum / successfulSamples;
    }

    Serial.print("Roll offset: ");
    Serial.println(rollOffset, 3);

    Serial.print("Pitch offset: ");
    Serial.println(pitchOffset, 3);

    Serial.print("Gyro Z bias : ");
    Serial.println(gyroZBias, 4);

    Serial.println("Calibration complete.");
    Serial.println();
}


// ============================================================
// VIBRATION
// ============================================================

bool readVibration()
{
    return digitalRead(VIBRATION_PIN) == HIGH;
}


// ============================================================
// SOIL MOISTURE
// ============================================================

int readSoil()
{
    return analogRead(SOIL_PIN);
}


// ============================================================
// BUZZER
// ============================================================

void warningBeep()
{
    digitalWrite(BUZZER_PIN, HIGH);
    delay(120);
    digitalWrite(BUZZER_PIN, LOW);
}


// ============================================================
// BUILD PACKET
// ============================================================

String buildPacket(
    unsigned long sequence,
    const IMUData &imu,
    bool vibration,
    int soil
)
{
    // Unified positional UG packet:
    // DATA,NODE_ID,SEQ,ROLL,PITCH,YAW,AX,AY,AZ,GX,GY,GZ,VIBRATION,SOIL_ADC

    String packet = "DATA,";

    packet += NODE_ID;
    packet += ",";

    packet += String(sequence);
    packet += ",";

    packet += String(imu.roll, 2);
    packet += ",";

    packet += String(imu.pitch, 2);
    packet += ",";

    packet += String(imu.yaw, 2);
    packet += ",";

    packet += String(imu.ax, 3);
    packet += ",";

    packet += String(imu.ay, 3);
    packet += ",";

    packet += String(imu.az, 3);
    packet += ",";

    packet += String(imu.gx, 3);
    packet += ",";

    packet += String(imu.gy, 3);
    packet += ",";

    packet += String(imu.gz, 3);
    packet += ",";

    packet += vibration ? "1" : "0";
    packet += ",";

    packet += String(soil);

    return packet;
}


// ============================================================
// SEND DATA + WAIT FOR ACK
// ============================================================

bool sendTelemetry(
    const String &packet,
    unsigned long sequence
)
{
    // Stop receive mode before transmitting.
    LoRa.idle();

    Serial.print("[LoRa TX] ");
    Serial.println(packet);

    LoRa.beginPacket();
    LoRa.print(packet);

    int result = LoRa.endPacket();

    if (result != 1)
    {
        Serial.println("[LoRa TX] FAILED");
        LoRa.receive();
        return false;
    }

    Serial.println("[LoRa TX] Sent");

    // Return immediately to RX and wait for the hub ACK.
    LoRa.receive();

    unsigned long startTime = millis();

    while (millis() - startTime < ACK_TIMEOUT_MS)
    {
        int packetSize = LoRa.parsePacket();

        if (packetSize)
        {
            String received = "";

            while (LoRa.available())
            {
                received += (char)LoRa.read();
            }

            received.trim();

            Serial.print("[LoRa RX] ");
            Serial.println(received);

            String expectedACK =
                "ACK," + String(NODE_ID) +
                "," + String(sequence);

            if (received == expectedACK)
            {
                Serial.println("[ACK] Valid ACK received");
                LoRa.receive();
                return true;
            }
        }

        delay(2);
    }

    Serial.println("[ACK] Timeout");
    LoRa.receive();
    return false;
}


// ============================================================
// SETUP
// ============================================================

void setup()
{
    Serial.begin(115200);

    delay(1000);

    Serial.println();
    Serial.println("==========================================");
    Serial.println("       TERRAVEIL UG-02 NODE");
    Serial.println("       Ground Monitoring Node");
    Serial.println("==========================================");

    // -------------------------
    // GPIO
    // -------------------------

    pinMode(VIBRATION_PIN, INPUT);
    pinMode(BUZZER_PIN, OUTPUT);

    digitalWrite(BUZZER_PIN, LOW);

    // -------------------------
    // ADC
    // -------------------------

    analogReadResolution(12);

    // -------------------------
    // I2C
    // -------------------------

    Wire.begin(
        SDA_PIN,
        SCL_PIN
    );

    Wire.setClock(400000);


    // -------------------------
    // MPU6050
    // -------------------------

    mpuWrite(
        0x6B,
        0x00
    );

    delay(100);

    // ±2g accelerometer
    mpuWrite(
        0x1C,
        0x00
    );

    // ±250°/s gyroscope
    mpuWrite(
        0x1B,
        0x00
    );

    Serial.println("MPU6050 initialized.");


    // -------------------------
    // LoRa
    // -------------------------

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

    Serial.println("Starting LoRa...");

    if (!LoRa.begin(LORA_FREQUENCY))
    {
        Serial.println("ERROR: LoRa initialization failed.");

        while (true)
        {
            warningBeep();
            delay(1000);
        }
    }

    // Same configuration as HOST-01
    LoRa.setSpreadingFactor(7);
    LoRa.setSignalBandwidth(125E3);
    LoRa.setCodingRate4(5);
    LoRa.setSyncWord(0x34);
    LoRa.enableCrc();

    LoRa.setTxPower(17);

    LoRa.idle();

    Serial.println("LoRa initialized.");
    Serial.println("Frequency: 433 MHz");
    Serial.println("SF: 7");
    Serial.println("Bandwidth: 125 kHz");
    Serial.println("Coding Rate: 4/5");


    // -------------------------
    // Calibration
    // -------------------------

    calibrateMPU();

    filteredYaw = 0.0f;
    lastIMUTime = millis();

    Serial.println();
    // Polling node stays in RX until HOST-01 addresses it.
    LoRa.receive();

    Serial.println("UG-02 READY - WAITING FOR HUB POLLS");
    Serial.println("==========================================");
    Serial.println();
}


// ============================================================
// LOOP
// ============================================================

void loop()
{
    // --------------------------------------------------------
    // Continuously refresh the latest sensor state.
    // Radio transmission is NOT timer-driven anymore.
    // --------------------------------------------------------

    static IMUData latestIMU = {};
    static bool haveValidIMU = false;

    IMUData currentIMU;

    if (readIMU(currentIMU))
    {
        latestIMU = currentIMU;
        haveValidIMU = true;
    }

    bool vibration = readVibration();
    int soil = readSoil();

    // Existing local warning behaviour.
    if (vibration)
    {
        Serial.println("[WARNING] Vibration detected!");
        warningBeep();
    }

    // --------------------------------------------------------
    // Listen for hub command.
    // Every node hears the poll, but ONLY the addressed node
    // responds. Example: POLL,UG-02
    // --------------------------------------------------------

    int packetSize = LoRa.parsePacket();

    if (packetSize > 0)
    {
        String received = "";

        while (LoRa.available())
        {
            received += (char)LoRa.read();
        }

        received.trim();

        String expectedPoll =
            "POLL," + String(NODE_ID);

        if (received == expectedPoll)
        {
            Serial.print("[POLL RX] ");
            Serial.println(received);

            if (!haveValidIMU)
            {
                Serial.println("[POLL] Cannot respond: no valid MPU6050 sample yet.");
                LoRa.receive();
                delay(2);
                return;
            }

            sequenceNumber++;

            String packet =
                buildPacket(
                    sequenceNumber,
                    latestIMU,
                    vibration,
                    soil
                );

            // Keep the familiar sensor print block.
            Serial.println();
            Serial.println("------------- SENSOR DATA -------------");

            Serial.print("Node ID      : ");
            Serial.println(NODE_ID);

            Serial.print("Sequence     : ");
            Serial.println(sequenceNumber);

            Serial.print("Roll         : ");
            Serial.print(latestIMU.roll, 2);
            Serial.println(" deg");

            Serial.print("Pitch        : ");
            Serial.print(latestIMU.pitch, 2);
            Serial.println(" deg");

            Serial.print("Vibration    : ");
            Serial.println(
                vibration ? "DETECTED" : "NORMAL"
            );

            Serial.print("Soil ADC     : ");
            Serial.println(soil);

            Serial.println("---------------------------------------");

            // Give HOST-01 a tiny deterministic turnaround window
            // to switch from TX (poll) back to RX.
            delay(POLL_TURNAROUND_MS);

            bool ack =
                sendTelemetry(
                    packet,
                    sequenceNumber
                );

            if (ack)
            {
                Serial.println("[STATUS] Telemetry delivered.");
            }
            else
            {
                Serial.println("[STATUS] Telemetry delivery failed.");
            }

            Serial.println();
        }
        else
        {
            // Polls for UG-02 / LD-01 are intentionally ignored.
            LoRa.receive();
        }
    }

    delay(5);
}
