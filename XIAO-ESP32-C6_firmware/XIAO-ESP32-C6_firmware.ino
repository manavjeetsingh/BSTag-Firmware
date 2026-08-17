#include <SPI.h>
#include "USB.h"


#define V1 D9
#define V2 D8
#define V3 D7

#define HSPI_MISO D2
#define HSPI_MOSI D4
#define HSPI_SCLK D3
#define HSPI_SS D1
#define BUFF_SIZE 10000
#define PWR_EN D5
#define TAG_LED D6


SPIClass *hspi = NULL;
unsigned long time_now;
bool adcEnable = false;
int long ReadData = 0;
float adc_value;
int long CHANNEL=1;
int long TEMP=0;
static constexpr float ADC_REF_MV = 5000.0;


int RFSW[8][3] = {{0, 0, 0}, 
                  {0, 0, 1}, 
                  {0, 1, 0}, 
                  {0, 1, 1}, 
                  {1, 0, 0}, 
                  {1, 0, 1}, 
                  {1, 1, 0}, 
                  {1, 1, 1}};

bool readBuffFlag=false;
// String readADCBuff="";
float readADCBuff[BUFF_SIZE];
unsigned int curBuffSize=0;
bool adcPlotter = false;


void RFswitch(int ch) {
  digitalWrite(V1, RFSW[ch][0]);
  digitalWrite(V2, RFSW[ch][1]);
  digitalWrite(V3, RFSW[ch][2]);
}
  

float GetAdcValue(){
  hspi->beginTransaction(SPISettings(40000000, MSBFIRST, SPI_MODE0));
  digitalWrite(HSPI_SS, LOW);    // CNV low -> SDO enabled, MSB presented
  uint16_t raw = hspi->transfer16(0);
  digitalWrite(HSPI_SS, HIGH);   // raise CS inside the transaction
  hspi->endTransaction();

  adc_value = (float)raw * ADC_REF_MV / 65535.0f;
  
  return adc_value;
}

void adc_setup() {
  digitalWrite(PWR_EN, HIGH);
  digitalWrite(TAG_LED, HIGH);

  hspi = new SPIClass(FSPI);
  hspi->begin(HSPI_SCLK, HSPI_MISO);

  pinMode(HSPI_MOSI, OUTPUT);
  digitalWrite(HSPI_MOSI, HIGH);

  pinMode(HSPI_SS, OUTPUT);
  digitalWrite(HSPI_SS, HIGH);
}


void setup() {
  pinMode(V1, OUTPUT);
  pinMode(V2, OUTPUT);
  pinMode(V3, OUTPUT);
  
  pinMode(PWR_EN, OUTPUT);
  pinMode(TAG_LED, OUTPUT);

  digitalWrite(V1, LOW);
  digitalWrite(V2, LOW);
  digitalWrite(V3, LOW);



  adc_setup();

  RFswitch(CHANNEL);

  for (int i=0; i<BUFF_SIZE; ++i) {
    readADCBuff[i]=0;
  }

  Serial.begin(115200);

}

void loop() {
  digitalWrite(TAG_LED, !adcPlotter);

  
  if (readBuffFlag==true) {
    // readADCBuff.concat(String(GetAdcValue(), 2));
    // readADCBuff.concat(",");
    if (curBuffSize<BUFF_SIZE) {
      readADCBuff[curBuffSize]=GetAdcValue();
      curBuffSize+=1;
    }
  }

  if (Serial.available() > 0) {
    
    String rev = Serial.readString();
    String cmd = rev.substring(0, 3);
    cmd.toLowerCase();

    if (cmd == "ch_") {

      // TODO: check if readbuff flag is on, do no change phase.
      
      int ch = rev.substring(3, 4).toInt() - 1;
      RFswitch(ch);
      CHANNEL=ch;
      char printf[50];
      sprintf(printf,"ch: %d, ok", ch+1);
      Serial.println(printf);
    
    } 
    
    else if (cmd == "adc") {
      
      int sw = rev.substring(3, 4).toInt();
      
      if (sw == 0) {
        adcEnable = false;
      } 
      else if (sw == 1) {
        adcEnable = true;
      } 
      else if (sw == 2||sw == 3||sw == 4||sw == 5) {
        String str = "";
        for(int i = 1; i < sw*100; i++ ){
            str.concat(String(GetAdcValue(), 2));
            str.concat(",");
        }
        Serial.println(str);
      }
      else {
        adcEnable = false;
      }
    }

    else if (cmd=="rdb") {
      // ReadBegin: Start reading from ADC and storing into a buffer
      int ch = 1;
      RFswitch(ch);
      readBuffFlag=true;
      Serial.println("rdb");
    }

    else if (cmd=="rds"){
      // ReadStop: Stop reading and send the read buffer 
      readBuffFlag=false;
      
      // Old, when buf is string
      // int buff_len=readADCBuff.length();
      // Serial.println(buff_len);
      // Serial.println(readADCBuff);

      Serial.print(curBuffSize);
      Serial.println(",");
      for (int i=0; i<curBuffSize; ++i) {
        Serial.print(readADCBuff[i],2);
        Serial.println(",");
      }
      Serial.println("end");
      
      // resetting buffer
      // readADCBuff="";
      for (int i=0; i<BUFF_SIZE; ++i) {
        readADCBuff[i]=0;
      }
      curBuffSize=0;
  
    }

    else if (cmd == "spl") {
      // set phase 2
      RFswitch(1);
      // start adc plotter
      adcPlotter=true;
      
    }

    else if (cmd == "epl") {
      // end adc plotter
      adcPlotter=false;
    }

    else if (cmd=="mpp") {
      int channels[]= {1,3,4,6,7,8};
      for (int mpp_idx=0; mpp_idx<1; mpp_idx++){
        // Do one complete MPP
        for (int ch_idx=0; ch_idx<6; ch_idx++){
          int ch = channels[ch_idx] - 1;
          RFswitch(ch);
          CHANNEL=ch;
          delay(100);
        }
      }
      
      Serial.println("mpp");
    
    }
    
    // else if (cmd == "mac"){
    //   Serial.println(WiFi.macAddress());
    //   // Serial.println(TEMP);
    //   // TEMP+=1;
    // }
    else {
      Serial.println("cmd: not found");
    }
  }

  if (adcPlotter==true){
    Serial.println(GetAdcValue()); 
    delay(50);
  }

}
