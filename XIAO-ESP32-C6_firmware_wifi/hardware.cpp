#include "hardware.h"

#include <SPI.h>

#include "config.h"

static SPIClass adc_spi(FSPI);

uint8_t current_channel = 2;

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

void hardwareInit(void)
{
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
}

void setStatusLed(bool on)
{
    digitalWrite(PIN_STATUS_LED, on ? HIGH : LOW);
}

void setUserLed(bool on)
{
    digitalWrite(PIN_USER_LED, on ? LOW : HIGH);
}

bool switchChannel(uint8_t channel)
{
    if (channel < 1 || channel > 8) {
        return false;
    }

    const uint8_t *levels = RF_SWITCH_TABLE[channel - 1];
    digitalWrite(PIN_RF_V1, levels[0]);
    digitalWrite(PIN_RF_V2, levels[1]);
    digitalWrite(PIN_RF_V3, levels[2]);
    current_channel = channel;
    return true;
}

uint16_t readAdcRaw(void)
{
    adc_spi.beginTransaction(SPISettings(SPI_CLOCK_HZ, MSBFIRST, SPI_MODE0));
    digitalWrite(PIN_SPI_CS, LOW);
    uint16_t raw = adc_spi.transfer16(0);
    digitalWrite(PIN_SPI_CS, HIGH);
    adc_spi.endTransaction();
    return raw;
}

float rawToMilliVolts(uint16_t raw)
{
    return (float)raw * ADC_REF_MV / 65535.0f;
}

uint16_t milliVoltsToRaw(float mv)
{
    if (mv <= 0.0f) {
        return 0;
    }
    float raw = mv * 65535.0f / ADC_REF_MV;
    return (raw >= 65535.0f) ? 65535 : (uint16_t)raw;
}
