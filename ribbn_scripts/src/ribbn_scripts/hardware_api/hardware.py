import serial
import socket
import pandas as pd
import numpy as np
import time
import json

WIFI_PORT = 3333
WIFI_TIMEOUT = 10 #sec

tag1_mac = b'EC:62:60:4D:34:8C\r\n'
tag2_mac = b'94:3C:C6:6D:53:5C\r\n'
tag3_mac = b'10:97:BD:D4:05:10\r\n'
tag4_mac = b'94:3C:C6:6D:29:2C\r\n'
pos_mac = [tag1_mac, tag2_mac, tag3_mac, tag4_mac]

class Exciter:
    def __init__(self):
        import pyvisa
        rm = pyvisa.ResourceManager()
        self.inst = rm.open_resource('GPIB0::19::INSTR')

    def set_freq(self, freq):
        freq_str = "FREQ:CW " + str(freq) + "MHZ"
        self.inst.write(freq_str)

    def set_pwr(self, pwr):
        pwr_str = "POW:AMPL " + str(pwr) + "DBM;:OUTP:STAT ON"
        self.inst.write(pwr_str)


class Tag:
    def __init__(self, com_str):
        self.com_str = com_str
        self.ser = None
        self.sock = None
        self._wifi_buf = b''
        self.connect()
        self.resetTime=60 #sec

    def connect(self):
        not_connected = 1
        while not_connected:
            try:
                # print("Connect started for",self.com_str)

                self.ser = serial.Serial(port=self.com_str, baudrate=115200, parity=serial.PARITY_NONE,
                                         stopbits=serial.STOPBITS_ONE, bytesize=serial.EIGHTBITS, timeout=0)
                not_connected = 0
                # print("Connect done for",self.com_str)

            except:
                print('couldnt connect. retrying in 1 sec')
                time.sleep(1)
                continue

    def disconnect(self):
        try:
            self.ser.close()
        except:
            print('error disconnecting')

    def connect_wifi(self, host, port=WIFI_PORT, timeout=WIFI_TIMEOUT):
        """
            Opens a TCP connection to the tag's WiFi command port (TCP_PORT
            in the firmware), the same one `nc <ip> 3333` talks to. This is
            the WiFi counterpart to connect(): the _wifi variants below
            send/receive the same text commands over this raw socket
            instead of over self.ser. No makefile()/buffered-stream layer
            is used here -- just send()/recv(), like nc.
        """
        not_connected = 1
        while not_connected:
            try:
                self.sock = socket.create_connection((host, port), timeout=timeout)
                self.sock.settimeout(0.05)
                self._wifi_buf = b''
                not_connected = 0
            except Exception:
                print('couldnt connect via wifi. retrying in 1 sec')
                time.sleep(1)
                continue
    
    def get_ip_port(self):
        while True:
            try:
                discard_read=self.ser.readline()
                self.ser.write(bytes("net\r\n", "UTF8"))
                line=b''
                while line==b'':
                    line = self.ser.readline()
                return json.loads(line.decode("UTF8"))
            except Exception as e:
                # self.disconnect()
                raise e # raising becuase don't want to do silent fail.

    def disconnect_wifi(self):
        try:
            self.sock.close()
        except Exception:
            print('error disconnecting wifi')

    def _wifi_write(self, data):
        self.sock.sendall(data)

    def _wifi_readline(self):
        """
            Raw-socket equivalent of self.ser.readline() with timeout=0:
            returns whatever line is available in the internal buffer, or
            b'' (without blocking) if a full line hasn't arrived yet.
        """
        nl = self._wifi_buf.find(b'\n')
        if nl == -1:
            try:
                chunk = self.sock.recv(65536)
            except socket.timeout:
                chunk = b''
            except OSError:
                chunk = b''
            if chunk:
                self._wifi_buf += chunk
            nl = self._wifi_buf.find(b'\n')

        if nl == -1:
            return b''

        line = self._wifi_buf[:nl + 1]
        self._wifi_buf = self._wifi_buf[nl + 1:]
        return line

    def reflect(self, ch):
        c_str = 'ch: ' + str(ch) + ', ok\r\n'
        c_str = bytes(c_str, "UTF8")

        while True:
            try:
                # self.connect()
                discard_read=self.ser.readline()
                # print("DR",discard_read)
                self.ser.write(bytes("ch_"+str(ch)+"\r\n", "UTF8"))
                ts = time.time()
                while True:
                    line = self.ser.readline()
                    # print(line)
                    if len(line) > 0:
                        print(line,'\t',c_str)
                        assert (line == c_str)
                        return

                    if time.time() - ts > 5:
                        raise Exception('no valid answer timeout')
            except Exception as e:
                # self.disconnect()
                raise e

    def reflect_wifi(self, ch, timeout=WIFI_TIMEOUT):
        c_str = 'ch: ' + str(ch) + ', ok\r\n'
        c_str = bytes(c_str, "UTF8")

        while True:
            try:
                discard_read = self._wifi_readline()
                self._wifi_write(bytes("ch_"+str(ch)+"\r\n", "UTF8"))
                ts = time.time()
                while True:
                    line = self._wifi_readline()
                    if len(line) > 0:
                        print(line, '\t', c_str)
                        assert (line == c_str)
                        return

                    if time.time() - ts > timeout:
                        raise Exception('no valid answer timeout')
            except Exception as e:
                raise e

    def begin_reading(self):
        """
            The tag starts reading ADC out and stores it in microcontroller
            memory. 
        """
        self.ser.write(b"rdb\0\n")
        command_start_time=time.time()
        while True:
            if time.time()-command_start_time>self.resetTime:
                raise Exception("Stuck in begin_reading")
            
            line = self.ser.readline()
            if len(line) > 0:
                if "rdb" in str(line):
                    break
                else:
                    print(str(line))

    def begin_reading_wifi(self, timeout=WIFI_TIMEOUT):
        """
            WiFi counterpart to begin_reading().
        """
        self._wifi_write(b"rdb\0\n")
        command_start_time = time.time()
        while True:
            if time.time() - command_start_time > timeout:
                raise Exception("Stuck in begin_reading_wifi")

            line = self._wifi_readline()
            if len(line) > 0:
                if "rdb" in str(line):
                    break
                else:
                    print(str(line))

    # def stop_reading(self):
    #     """
    #         Stops the continuous reading and storing of ADC out.
    #         Ideal tag output would be in the following format.
    #         "num_readings,\r\n"
    #         "reading 1,\r\n"
    #         "reading 2,\r\n"
    #         ..
    #         "reading n,\r\n"
    #
    #        Tag output might not be ideal always, it can have some artifacts.
    #        This function processes that and returns the values as an array.
    #     """
    #     self.ser.write(b"rds\0\n")
    #     voltages=""
    #
    #     read_start_time=time.time()
    #     while True:
    #         if time.time()-read_start_time>self.resetTime:
    #             break
    #         line = self.ser.readline()
    #         if len(line) > 0:
    #             # ss = str(line)
    #             ss = str(line)
    #             # print(ss)
    #
    #             if "end" in ss:
    #                 break
    #             try:
    #                 to_add=line.decode()
    #                 to_add=to_add.replace("\n",'')
    #                 to_add=to_add.replace("\r",'')
    #                 voltages+=to_add
    #             except Exception as e:
    #                 print(e)
    #     return self.clean_voltage_data(voltages)

    def stop_reading(self):
        """
            Stops the continuous reading and requests the buffered ADC data.
            Tag output is a JSON blob of the form:
            {"info":"buf","ch":2,"unit":"mV","count":10000,"full":1,"data":"1.678,1.526,...,3.052"}

            Since the serial port is opened with timeout=0, readline() returns
            whatever bytes happen to be buffered at each poll rather than whole
            lines, so the JSON arrives split across many reads. Bytes are
            accumulated here until a balanced {...} blob can be parsed.
        """
        self.ser.write(b"rds\0\n")

        buffer = ""
        read_start_time = time.time()
        while True:
            if time.time() - read_start_time > self.resetTime:
                raise Exception("Stuck in stop_reading")

            line = self.ser.readline()
            if len(line) == 0:
                continue

            try:
                buffer += line.decode(errors='ignore')
            except Exception as e:
                print(e)
                continue

            start = buffer.find('{')
            end = buffer.rfind('}')
            if start == -1 or end == -1 or end < start:
                continue

            try:
                payload = json.loads(buffer[start:end + 1])
            except Exception:
                continue

            return self.clean_buf_data(payload)

    def stop_reading_wifi(self, timeout=WIFI_TIMEOUT):
        """
            WiFi counterpart to stop_reading(). Same JSON-blob buffering
            approach applies: a 10000-sample dump spans many TCP segments,
            so bytes are accumulated until a balanced {...} can be parsed.
        """
        self._wifi_write(b"rds\0\n")

        buffer = ""
        read_start_time = time.time()
        while True:
            if time.time() - read_start_time > timeout:
                raise Exception("Stuck in stop_reading_wifi")

            line = self._wifi_readline()
            if len(line) == 0:
                continue

            try:
                buffer += line.decode(errors='ignore')
            except Exception as e:
                print(e)
                continue

            start = buffer.find('{')
            end = buffer.rfind('}')
            if start == -1 or end == -1 or end < start:
                continue

            try:
                payload = json.loads(buffer[start:end + 1])
            except Exception:
                continue

            return self.clean_buf_data(payload)

    def clean_buf_data(self, payload):
        """
            Parses the "data" field of a buffer JSON payload into an array of floats.
        """
        data = payload["data"].split(',')
        clean_data = []
        for d in data:
            try:
                clean_data.append(float(d))
            except Exception as e:
                print(e)
        assert (len(clean_data) <= payload["count"])

        return np.array(clean_data)
    
    def clean_voltage_data(self, voltages):
        """
            Cleans the tag output data and converts it to an array.
        """
        data=voltages.split(',')
        max_num=int(data[0])
        data=data[1:]
        clean_data=[]
        for d in data:
            try:
                clean_data.append(float(d))
            except Exception as e:
                print(e)
        assert(len(clean_data)<=max_num)
        
        return np.array(clean_data)

    def perform_mpp(self, passes=1):
        """
            Asking the tag to do the MPP, tag stays at each phase for 100ms.
            This is a blocking call.
            Returns MPP start and end times.
        """
        mpp_start_time=time.time()
        self.ser.write(f"mpp_{passes}\0\n".encode())
        while True:
            if time.time()-mpp_start_time>self.resetTime:
                raise Exception("Stuch in perfrom_mpp")
                
            line = self.ser.readline()
            if len(line) > 0:
                if "mpp" in str(line):
                    break
                else:
                    print(str(line))
        mpp_end_time=time.time()

        return mpp_start_time, mpp_end_time

    def perform_mpp_wifi(self, passes=1, timeout=WIFI_TIMEOUT):
        """
            WiFi counterpart to perform_mpp().
        """
        mpp_start_time = time.time()
        self._wifi_write(f"mpp_{passes}\0\n".encode())
        while True:
            if time.time() - mpp_start_time > timeout:
                raise Exception("Stuch in perfrom_mpp_wifi")

            line = self._wifi_readline()
            if len(line) > 0:
                if "mpp" in str(line):
                    break
                else:
                    print(str(line))
        mpp_end_time = time.time()

        return mpp_start_time, mpp_end_time

    def startPlotting(self):
        self.ser.write(b"spl\0\n")

    def endPlotting(self):
        self.ser.write(b"epl\0\n")

    def startPlotting_wifi(self):
        self._wifi_write(b"spl\0\n")

    def endPlotting_wifi(self):
        self._wifi_write(b"epl\0\n")

    def get_adc_val(self):
        # try:
        #     assert (self.get_mac() is not None)
        # except:
        #     print("Invalid COM port MAC combination")
        #     return None

        while True:
            try:
                # self.connect()
                discard_read=self.ser.readline()
                # print("DR",discard_read)
                self.ser.write(b'adc30\0\n')
                ts = time.time()
                while True:
                    line = self.ser.readline()
                    if len(line) > 0:
                        ss = str(line).split(',')
                        assert (len(ss) > 5)
                        # print(line)
                        # v = np.median(np.array(ss[1:-1]).astype(float))
                        v = np.array(ss[1:-1]).astype(float)
                        # self.disconnect()
                        return v

                    if time.time() - ts > 5:
                        raise Exception('no valid answer timeout')
            except Exception as e:
                print(e)
                raise e
            finally:
                # self.disconnect()
                pass

    def get_adc_val_wifi(self, timeout=WIFI_TIMEOUT):
        """
            WiFi counterpart to get_adc_val().
        """
        while True:
            try:
                discard_read = self._wifi_readline()
                self._wifi_write(b'adc30\0\n')
                ts = time.time()
                while True:
                    line = self._wifi_readline()
                    if len(line) > 0:
                        ss = str(line).split(',')
                        assert (len(ss) > 5)
                        v = np.array(ss[1:-1]).astype(float)
                        return v

                    if time.time() - ts > timeout:
                        raise Exception('no valid answer timeout')
            except Exception as e:
                print(e)
                raise e
            finally:
                pass

    def get_mac(self):

        while True:
            try:
                discard_read=self.ser.readline()
                # print("DR",discard_read)
                self.ser.write(b'mac\0\n')
                ts = time.time()
                while True:
                    line = self.ser.readline()
                    if len(line) > 0:
                        # assert (line in pos_mac.keys() and pos_mac[line]==self.com_str)
                        # print(line)
                        return line

                    if time.time() - ts > 5:
                        raise Exception('no valid answer timeout')
            except Exception as e:
                print(e)
            finally:
                pass
                # self.disconnect()

    def get_mac_wifi(self, timeout=WIFI_TIMEOUT):
        """
            WiFi counterpart to get_mac().
        """
        while True:
            try:
                discard_read = self._wifi_readline()
                self._wifi_write(b'mac\0\n')
                ts = time.time()
                while True:
                    line = self._wifi_readline()
                    if len(line) > 0:
                        return line

                    if time.time() - ts > timeout:
                        raise Exception('no valid answer timeout')
            except Exception as e:
                print(e)
            finally:
                pass


class VNA:
    def __init__(self):
        import pyvisa
        rm = pyvisa.ResourceManager()
        self.inst = rm.open_resource('GPIB0::17::INSTR')
        self.inst.write(':SENS1:FREQ:STAR 700E6')
        self.inst.write(':SENS1:FREQ:STOP 1000E6')
        self.inst.write(':SOUR1:POW -30')
        self.inst.write(':SENS1:SWE:DEL 0.001')
        self.inst.write(':SENS1:SWE:POIN 200')
        self.inst.write(':CALC1:PAR1:DEF S11')
        # self.inst.write(':MMEM:STOR:FDAT "D:/automated_folder/Trace01.csv"')

    def wtf(self, str):
        x = ':MMEM:STOR:FDAT "D:' + str + '.csv"'
        self.inst.write(x)

    def set_pwr(self, pwr):
        self.inst.write(':SOUR1:POW ' + str(pwr) + '')

    def transfer_file(self, src, dst):
        self.inst.write(':MMEM:TRAN? "D:' + src + '.csv"')
        f = self.inst.read()
        from io import StringIO
        io = StringIO(f)
        pd.DataFrame(pd.read_csv(io, skiprows=2)).to_csv(dst)

#
# vna = VNA()
# vna.set_pwr(-6)
# vna.wtf('/automated_folder/file_name')
# f = vna.transfer_file('/automated_folder/file_name', 'a.csv')
