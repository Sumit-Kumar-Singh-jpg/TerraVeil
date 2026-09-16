#include <Arduino.h>
#include <Wire.h>
#include <SPI.h>
#include <LoRa.h>

// ============================================================
// TERRAVEIL LD-01
// Linear Displacement Monitoring Node
//
// Sensors:
//   - Type-B Linear Potentiometer
//   - MPU6050
//
// Outputs:
//   - displacement mm
//   - potentiometer ADC
//   - roll / pitch / relative yaw
//   - ax / ay / az
//   - gx / gy / gz
// ============================================================


// ============================================================
// NODE CONFIGURATION
// ============================================================

const char* NODE_ID = "LD-01";


// ============================================================
// LINEAR POTENTIOMETER
// ============================================================

#define POT_PIN 34

const int ADC_SAMPLES = 20;


// ------------------------------------------------------------
// POT CALIBRATION
//
// Replace these after actual calibration.
// ------------------------------------------------------------

int POT_ADC_MIN = 0;
int POT_ADC_MAX = 4095;

float POT_TRAVEL_MM = 100.0f;


// ============================================================
// MPU6050
// ============================================================

#define MPU_ADDR 0x68

#define SDA_PIN 21
#define SCL_PIN 22

const int CALIBRATION_SAMPLES = 500;

float rollOffset = 0.0f;
float pitchOffset = 0.0f;
float gyroZBias = 0.0f;


// ============================================================
// LORA RA-02
// ============================================================

#define LORA_SS   5
#define LORA_RST  14
#define LORA_DIO0 26

#define LORA_FREQUENCY 433E6

#define LORA_SCK  18
#define LORA_MISO 19
#define LORA_MOSI 23


// ============================================================
// TIMING
// ============================================================

const unsigned long SEND_INTERVAL_MS = 5000;
const unsigned long ACK_TIMEOUT_MS   = 1500;


// ============================================================
// STATE
// ============================================================

unsigned long lastSendTime = 0;
unsigned long sequenceNumber = 0;

unsigned long lastIMUTime = 0;


// ============================================================
// FILTER STATE
// ============================================================

float filteredRoll  = 0.0f;
float filteredPitch = 0.0f;

// Relative yaw only.
// MPU6050 has no magnetometer.
float filteredYaw = 0.0f;


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
// MPU6050 LOW LEVEL WRITE
// ============================================================

void mpuWrite(
    byte reg,
    byte data
)
{
    Wire.beginTransmission(MPU_ADDR);

    Wire.write(reg);
    Wire.write(data);

    Wire.endTransmission();
}


// ============================================================
// MPU6050 RAW READ
// ============================================================

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


    Wire.requestFrom(
        MPU_ADDR,
        14,
        true
    );


    if (Wire.available() < 14)
    {
        return false;
    }


    // Accelerometer

    ax =
        (Wire.read() << 8)
        |
        Wire.read();

    ay =
        (Wire.read() << 8)
        |
        Wire.read();

    az =
        (Wire.read() << 8)
        |
        Wire.read();


    // Temperature - ignored

    Wire.read();
    Wire.read();


    // Gyroscope

    gx =
        (Wire.read() << 8)
        |
        Wire.read();

    gy =
        (Wire.read() << 8)
        |
        Wire.read();

    gz =
        (Wire.read() << 8)
        |
        Wire.read();


    return true;
}


// ============================================================
// MPU CALIBRATION
// ============================================================

void calibrateMPU()
{
    Serial.println();

    Serial.println(
        "================================"
    );

    Serial.println(
        "MPU6050 CALIBRATION"
    );

    Serial.println(
        "Keep LD-01 completely still."
    );

    Serial.println(
        "================================"
    );


    delay(2000);


    float rollSum = 0.0f;
    float pitchSum = 0.0f;
    float gyroZSum = 0.0f;

    int successfulSamples = 0;


    for (
        int i = 0;
        i < CALIBRATION_SAMPLES;
        i++
    )
    {
        int16_t axRaw;
        int16_t ayRaw;
        int16_t azRaw;

        int16_t gxRaw;
        int16_t gyRaw;
        int16_t gzRaw;


        if (
            mpuReadRaw(
                axRaw,
                ayRaw,
                azRaw,
                gxRaw,
                gyRaw,
                gzRaw
            )
        )
        {
            float ax =
                axRaw / 16384.0f;

            float ay =
                ayRaw / 16384.0f;

            float az =
                azRaw / 16384.0f;


            float roll =
                atan2(
                    ay,
                    az
                )
                * 180.0f
                / PI;


            float pitch =
                atan2(
                    -ax,
                    sqrt(
                        ay * ay
                        +
                        az * az
                    )
                )
                * 180.0f
                / PI;


            float gz =
                gzRaw / 131.0f;


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
            rollSum
            /
            successfulSamples;


        pitchOffset =
            pitchSum
            /
            successfulSamples;


        gyroZBias =
            gyroZSum
            /
            successfulSamples;
    }


    Serial.print("Roll offset  : ");
    Serial.println(rollOffset, 3);

    Serial.print("Pitch offset : ");
    Serial.println(pitchOffset, 3);

    Serial.print("Gyro Z bias  : ");
    Serial.println(gyroZBias, 4);

    Serial.println("Calibration complete.");
    Serial.println();
}


// ============================================================
// READ IMU
// ============================================================

bool readIMU(
    IMUData &imu
)
{
    int16_t axRaw;
    int16_t ayRaw;
    int16_t azRaw;

    int16_t gxRaw;
    int16_t gyRaw;
    int16_t gzRaw;


    if (
        !mpuReadRaw(
            axRaw,
            ayRaw,
            azRaw,
            gxRaw,
            gyRaw,
            gzRaw
        )
    )
    {
        return false;
    }


    // ========================================================
    // CONVERT TO PHYSICAL UNITS
    // ========================================================

    // Accelerometer ±2g
    // 16384 LSB/g

    imu.ax =
        axRaw / 16384.0f;

    imu.ay =
        ayRaw / 16384.0f;

    imu.az =
        azRaw / 16384.0f;


    // Gyroscope ±250 deg/s
    // 131 LSB/(deg/s)

    imu.gx =
        gxRaw / 131.0f;

    imu.gy =
        gyRaw / 131.0f;

    imu.gz =
        (gzRaw / 131.0f)
        -
        gyroZBias;


    // ========================================================
    // ACCELEROMETER ORIENTATION
    // ========================================================

    float accelRoll =
        atan2(
            imu.ay,
            imu.az
        )
        *
        180.0f
        /
        PI;


    float accelPitch =
        atan2(
            -imu.ax,
            sqrt(
                imu.ay * imu.ay
                +
                imu.az * imu.az
            )
        )
        *
        180.0f
        /
        PI;


    // ========================================================
    // FIRST READING
    // ========================================================

    if (lastIMUTime == 0)
    {
        filteredRoll =
            accelRoll;

        filteredPitch =
            accelPitch;

        filteredYaw =
            0.0f;


        lastIMUTime =
            millis();


        imu.roll =
            filteredRoll
            -
            rollOffset;


        imu.pitch =
            filteredPitch
            -
            pitchOffset;


        imu.yaw =
            filteredYaw;


        return true;
    }


    // ========================================================
    // DELTA TIME
    // ========================================================

    unsigned long now =
        millis();


    float dt =
        (
            now
            -
            lastIMUTime
        )
        /
        1000.0f;


    lastIMUTime =
        now;


    if (
        dt <= 0.0f
        ||
        dt > 1.0f
    )
    {
        dt = 0.01f;
    }


    // ========================================================
    // GYRO INTEGRATION
    // ========================================================

    float gyroRoll =
        filteredRoll
        +
        imu.gx * dt;


    float gyroPitch =
        filteredPitch
        +
        imu.gy * dt;


    filteredYaw +=
        imu.gz * dt;


    // ========================================================
    // COMPLEMENTARY FILTER
    // ========================================================

    const float alpha =
        0.98f;


    filteredRoll =
        alpha
        *
        gyroRoll
        +
        (
            1.0f
            -
            alpha
        )
        *
        accelRoll;


    filteredPitch =
        alpha
        *
        gyroPitch
        +
        (
            1.0f
            -
            alpha
        )
        *
        accelPitch;


    // ========================================================
    // WRAP RELATIVE YAW
    // ========================================================

    if (filteredYaw > 180.0f)
    {
        filteredYaw -= 360.0f;
    }

    else if (filteredYaw < -180.0f)
    {
        filteredYaw += 360.0f;
    }


    // ========================================================
    // FINAL ORIENTATION
    // ========================================================

    imu.roll =
        filteredRoll
        -
        rollOffset;


    imu.pitch =
        filteredPitch
        -
        pitchOffset;


    imu.yaw =
        filteredYaw;


    return true;
}


// ============================================================
// READ POTENTIOMETER
// ============================================================

int readPotentiometer()
{
    uint32_t total = 0;


    for (
        int i = 0;
        i < ADC_SAMPLES;
        i++
    )
    {
        total +=
            analogRead(
                POT_PIN
            );


        delay(2);
    }


    return
        total
        /
        ADC_SAMPLES;
}


// ============================================================
// ADC -> DISPLACEMENT
// ============================================================

float adcToDisplacement(
    int adc
)
{
    if (
        POT_ADC_MAX
        ==
        POT_ADC_MIN
    )
    {
        return 0.0f;
    }


    float displacement =
        (
            (float)
            (
                adc
                -
                POT_ADC_MIN
            )
            /
            (float)
            (
                POT_ADC_MAX
                -
                POT_ADC_MIN
            )
        )
        *
        POT_TRAVEL_MM;


    displacement =
        constrain(
            displacement,
            0.0f,
            POT_TRAVEL_MM
        );


    return displacement;
}


// ============================================================
// BUILD PACKET
// ============================================================
//
// FORMAT:
//
// DATA,
// NODE_ID,
// SEQ,
// DISP_MM,
// POT_ADC,
// ROLL,
// PITCH,
// YAW,
// AX,
// AY,
// AZ,
// GX,
// GY,
// GZ
//
// Example:
//
// DATA,LD-01,27,42.75,2184,
// -1.25,0.82,4.10,
// 0.021,-0.018,0.998,
// 0.140,-0.220,0.075
//
// ============================================================

String buildPacket(
    unsigned long sequence,
    float displacement,
    int potADC,
    const IMUData &imu
)
{
    String packet = "DATA,";

    packet += NODE_ID;
    packet += ",";

    packet += String(sequence);
    packet += ",";

    packet += String(displacement, 2);
    packet += ",";

    packet += String(potADC);
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
    // ========================================================
    // TRANSMIT
    // ========================================================

    Serial.print(
        "[LoRa TX] "
    );

    Serial.println(
        packet
    );


    LoRa.beginPacket();

    LoRa.print(
        packet
    );


    int result =
        LoRa.endPacket();


    if (
        result != 1
    )
    {
        Serial.println(
            "[LoRa TX] FAILED"
        );

        return false;
    }


    Serial.println(
        "[LoRa TX] Sent"
    );


    // ========================================================
    // WAIT FOR ACK
    // ========================================================

    LoRa.receive();


    unsigned long startTime =
        millis();


    while (
        millis()
        -
        startTime
        <
        ACK_TIMEOUT_MS
    )
    {
        int packetSize =
            LoRa.parsePacket();


        if (packetSize)
        {
            String received =
                "";


            while (
                LoRa.available()
            )
            {
                received +=
                    (char)
                    LoRa.read();
            }


            received.trim();


            Serial.print(
                "[LoRa RX] "
            );

            Serial.println(
                received
            );


            // Expected:
            //
            // ACK,LD-01,27

            String expectedACK =
                "ACK,"
                +
                String(NODE_ID)
                +
                ","
                +
                String(sequence);


            if (
                received
                ==
                expectedACK
            )
            {
                Serial.println(
                    "[ACK] Valid ACK received"
                );


                LoRa.idle();


                return true;
            }
        }


        delay(5);
    }


    LoRa.idle();


    Serial.println(
        "[ACK] Timeout"
    );


    return false;
}


// ============================================================
// SETUP
// ============================================================

void setup()
{
    Serial.begin(
        115200
    );


    delay(1000);


    Serial.println();

    Serial.println(
        "=========================================="
    );

    Serial.println(
        "       TERRAVEIL LD-01 NODE"
    );

    Serial.println(
        " Linear Displacement + Orientation Node"
    );

    Serial.println(
        "=========================================="
    );


    // ========================================================
    // ADC
    // ========================================================

    pinMode(
        POT_PIN,
        INPUT
    );


    analogReadResolution(
        12
    );


    analogSetPinAttenuation(
        POT_PIN,
        ADC_11db
    );


    // ========================================================
    // I2C
    // ========================================================

    Wire.begin(
        SDA_PIN,
        SCL_PIN
    );


    Wire.setClock(
        400000
    );


    // ========================================================
    // MPU6050
    // ========================================================

    // Wake up MPU6050

    mpuWrite(
        0x6B,
        0x00
    );


    delay(100);


    // Accelerometer ±2g

    mpuWrite(
        0x1C,
        0x00
    );


    // Gyroscope ±250 deg/s

    mpuWrite(
        0x1B,
        0x00
    );


    Serial.println(
        "MPU6050 initialized."
    );


    // ========================================================
    // LORA
    // ========================================================

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


    Serial.println(
        "Starting LoRa..."
    );


    if (
        !LoRa.begin(
            LORA_FREQUENCY
        )
    )
    {
        Serial.println(
            "ERROR: LoRa initialization failed."
        );


        while (true)
        {
            delay(1000);
        }
    }


    // ========================================================
    // SAME NETWORK CONFIG AS UG / HOST-01
    // ========================================================

    LoRa.setSpreadingFactor(
        7
    );


    LoRa.setSignalBandwidth(
        125E3
    );


    LoRa.setCodingRate4(
        5
    );


    LoRa.setSyncWord(
        0x34
    );


    LoRa.enableCrc();


    LoRa.setTxPower(
        17
    );


    LoRa.idle();


    Serial.println(
        "LoRa initialized."
    );

    Serial.println(
        "Frequency: 433 MHz"
    );

    Serial.println(
        "SF: 7"
    );

    Serial.println(
        "Bandwidth: 125 kHz"
    );

    Serial.println(
        "Coding Rate: 4/5"
    );


    // ========================================================
    // CALIBRATION
    // ========================================================

    calibrateMPU();


    filteredYaw =
        0.0f;


    lastIMUTime =
        millis();


    Serial.println();

    Serial.println(
        "LD-01 READY"
    );

    Serial.println(
        "=========================================="
    );

    Serial.println();
}


// ============================================================
// LOOP
// ============================================================

void loop()
{
    unsigned long now =
        millis();


    // ========================================================
    // READ POTENTIOMETER
    // ========================================================

    int potADC =
        readPotentiometer();


    float displacement =
        adcToDisplacement(
            potADC
        );


    // ========================================================
    // READ MPU6050
    // ========================================================

    IMUData imu;


    bool imuOK =
        readIMU(
            imu
        );


    // ========================================================
    // PERIODIC TRANSMISSION
    // ========================================================

    if (
        now
        -
        lastSendTime
        >=
        SEND_INTERVAL_MS
    )
    {
        lastSendTime =
            now;


        sequenceNumber++;


        if (!imuOK)
        {
            Serial.println(
                "[ERROR] MPU6050 read failed."
            );

            return;
        }


        // ====================================================
        // BUILD PACKET
        // ====================================================

        String packet =
            buildPacket(
                sequenceNumber,
                displacement,
                potADC,
                imu
            );


        // ====================================================
        // PRINT SENSOR DATA
        // ====================================================

        Serial.println();

        Serial.println(
            "------------- SENSOR DATA -------------"
        );


        Serial.print(
            "Node ID      : "
        );

        Serial.println(
            NODE_ID
        );


        Serial.print(
            "Sequence     : "
        );

        Serial.println(
            sequenceNumber
        );


        // ----------------------------------------------------
        // Linear displacement
        // ----------------------------------------------------

        Serial.print(
            "Displacement : "
        );

        Serial.print(
            displacement,
            2
        );

        Serial.println(
            " mm"
        );


        Serial.print(
            "Pot ADC      : "
        );

        Serial.println(
            potADC
        );


        // ----------------------------------------------------
        // Orientation
        // ----------------------------------------------------

        Serial.print(
            "Roll         : "
        );

        Serial.print(
            imu.roll,
            2
        );

        Serial.println(
            " deg"
        );


        Serial.print(
            "Pitch        : "
        );

        Serial.print(
            imu.pitch,
            2
        );

        Serial.println(
            " deg"
        );


        Serial.print(
            "Yaw          : "
        );

        Serial.print(
            imu.yaw,
            2
        );

        Serial.println(
            " deg"
        );


        // ----------------------------------------------------
        // Accelerometer
        // ----------------------------------------------------

        Serial.print(
            "Accel X      : "
        );

        Serial.print(
            imu.ax,
            3
        );

        Serial.println(
            " g"
        );


        Serial.print(
            "Accel Y      : "
        );

        Serial.print(
            imu.ay,
            3
        );

        Serial.println(
            " g"
        );


        Serial.print(
            "Accel Z      : "
        );

        Serial.print(
            imu.az,
            3
        );

        Serial.println(
            " g"
        );


        // ----------------------------------------------------
        // Gyroscope
        // ----------------------------------------------------

        Serial.print(
            "Gyro X       : "
        );

        Serial.print(
            imu.gx,
            3
        );

        Serial.println(
            " deg/s"
        );


        Serial.print(
            "Gyro Y       : "
        );

        Serial.print(
            imu.gy,
            3
        );

        Serial.println(
            " deg/s"
        );


        Serial.print(
            "Gyro Z       : "
        );

        Serial.print(
            imu.gz,
            3
        );

        Serial.println(
            " deg/s"
        );


        Serial.println(
            "---------------------------------------"
        );


        // ====================================================
        // SEND TO HOST-01
        // ====================================================

        bool ack =
            sendTelemetry(
                packet,
                sequenceNumber
            );


        if (ack)
        {
            Serial.println(
                "[STATUS] Telemetry delivered."
            );
        }

        else
        {
            Serial.println(
                "[STATUS] Telemetry delivery failed."
            );
        }


        Serial.println();
    }


    delay(10);
}
