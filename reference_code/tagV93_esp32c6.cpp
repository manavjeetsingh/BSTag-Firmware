/*
  firmware for TagV93 ESP32-C6.

  Commands over USB Serial:
    ch_<1-8>     Switch RF/tag channel
    adc          Read one external SPI ADC sample
    adc_<count>  Read count external SPI ADC samples, max 1000
    help         Print command list
  @Author Yang Xie
  @Date 07/20/2026
*/

#include <Arduino.h>
#include <SPI.h>
#include <driver/gpio.h>

// XIAO ESP32-C6 pin mapping by physical XIAO header position.
static constexpr uint8_t PIN_RF_V1 = 20;      // D9
static constexpr uint8_t PIN_RF_V2 = 19;      // D8
static constexpr uint8_t PIN_RF_V3 = 17;      // D7
static constexpr uint8_t PIN_SPI_MISO = 2;    // D2
static constexpr uint8_t PIN_SPI_MOSI = 22;   // D4
static constexpr uint8_t PIN_SPI_SCLK = 21;   // D3
static constexpr uint8_t PIN_SPI_CS = 1;      // D1
static constexpr uint8_t PIN_POWER_EN = 23;   // D5
static constexpr uint8_t PIN_STATUS_LED = 16; // D6
static constexpr uint8_t PIN_USER_LED = 15;   // Built-in LED, active-low
static constexpr uint8_t POWER_EN_ACTIVE_LEVEL = HIGH;

static constexpr uint32_t SERIAL_BAUD = 921600;
static constexpr uint32_t SPI_CLOCK_HZ = 40000000;
static constexpr uint16_t MAX_ADC_SAMPLES = 1000;
static constexpr float ADC_REF_MV = 5000.0f;

static SPIClass adc_spi(FSPI);
static uint8_t current_channel = 2;

static const uint8_t RF_SWITCH_TABLE[8][3] = {
    {0, 0, 0},
    {0, 0, 1},
    {0, 1, 0},
    {0, 1, 1},
    {1, 0, 0},
    {1, 0, 1},
    {1, 1, 0},
    {1, 1, 1},
};

static void setStatusLed(bool on)
{
    digitalWrite(PIN_STATUS_LED, on ? HIGH : LOW);
}

static void setUserLed(bool on)
{
    digitalWrite(PIN_USER_LED, on ? LOW : HIGH);
}

static bool switchChannel(uint8_t channel)
{
    if (channel < 1 || channel > 8) {
        return false;
    }

    const uint8_t* levels = RF_SWITCH_TABLE[channel - 1];
    digitalWrite(PIN_RF_V1, levels[0]);
    digitalWrite(PIN_RF_V2, levels[1]);
    digitalWrite(PIN_RF_V3, levels[2]);
    current_channel = channel;
    return true;
}

static uint16_t readAdcRaw()
{
    adc_spi.beginTransaction(SPISettings(SPI_CLOCK_HZ, MSBFIRST, SPI_MODE0));
    digitalWrite(PIN_SPI_CS, LOW);
    uint16_t raw = adc_spi.transfer16(0);
    digitalWrite(PIN_SPI_CS, HIGH);
    adc_spi.endTransaction();
    return raw;
}

static float rawToMilliVolts(uint16_t raw)
{
    return (float)raw * ADC_REF_MV / 65535.0f;
}

static void printHelp()
{
    Serial.println("Commands:");
    Serial.println("  ch_<1-8>     switch RF/tag channel");
    Serial.println("  adc          read one ADC sample");
    Serial.println("  adc_<count>  read ADC samples, max 1000");
    Serial.println("  adcraw       read one raw ADC code");
    Serial.println("  adcraw_<n>   read raw ADC codes, max 1000");
    Serial.println("  help         show this message");
}

static void handleCommand(String command)
{
    command.trim();
    if (command.length() == 0) {
        return;
    }

    if (command == "help" || command == "?") {
        printHelp();
        return;
    }

    if (command.startsWith("ch_")) {
        uint8_t channel = (uint8_t)command.substring(3).toInt();
        if (switchChannel(channel)) {
            Serial.printf("ch:%u, ok\n", current_channel);
        } else {
            Serial.println("ch:invalid, use ch_1 ... ch_8");
        }
        return;
    }

    if (command == "adc" || command.startsWith("adc_") ||
        command == "adcraw" || command.startsWith("adcraw_")) {
        bool raw_output = command == "adcraw" || command.startsWith("adcraw_");
        uint16_t count = 1;
        if (command.startsWith("adcraw_")) {
            count = (uint16_t)command.substring(7).toInt();
        } else if (command.startsWith("adc_")) {
            count = (uint16_t)command.substring(4).toInt();
        }
        if (command.startsWith("adcraw_") || command.startsWith("adc_")) {
            if (count == 0) {
                count = 1;
            }
            if (count > MAX_ADC_SAMPLES) {
                count = MAX_ADC_SAMPLES;
            }
        }

        Serial.print("{\"info\":\"adc\",\"ch\":");
        Serial.print(current_channel);
        Serial.print(",\"unit\":\"");
        Serial.print(raw_output ? "raw" : "mV");
        Serial.print("\",\"data\":\"");
        for (uint16_t i = 0; i < count; i++) {
            uint16_t raw = readAdcRaw();
            if (raw_output) {
                Serial.print(raw);
            } else {
                Serial.print(rawToMilliVolts(raw), 3);
            }
            if (i + 1 < count) {
                Serial.print(",");
            }
        }
        Serial.println("\"}");
        return;
    }

    Serial.print("cmd:not found, ");
    Serial.println(command);
}

void setup()
{
    Serial.begin(SERIAL_BAUD);
    uint32_t serial_wait_start = millis();
    while (!Serial && millis() - serial_wait_start < 2000) {
        delay(10);
    }

    pinMode(PIN_POWER_EN, OUTPUT);
    digitalWrite(PIN_POWER_EN, POWER_EN_ACTIVE_LEVEL);
    delay(100);

    pinMode(PIN_STATUS_LED, OUTPUT);
    pinMode(PIN_USER_LED, OUTPUT);
    setStatusLed(true);
    setUserLed(false);

    pinMode(PIN_RF_V1, OUTPUT);
    pinMode(PIN_RF_V2, OUTPUT);
    pinMode(PIN_RF_V3, OUTPUT);
    switchChannel(current_channel);

    adc_spi.begin(PIN_SPI_SCLK, PIN_SPI_MISO, PIN_SPI_MOSI, PIN_SPI_CS);
    pinMode(PIN_SPI_CS, OUTPUT);
    digitalWrite(PIN_SPI_CS, HIGH);
    pinMode(PIN_SPI_MOSI, OUTPUT);
    digitalWrite(PIN_SPI_MOSI, HIGH);

    Serial.println();
    Serial.println("TagV93 ready");
    printHelp();
}

void loop()
{
    if (Serial.available() > 0) {
        String command = Serial.readStringUntil('\n');
        setUserLed(true);
        handleCommand(command);
        setUserLed(false);
    }
}
