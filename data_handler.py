#!/usr/bin/env python3

'''
Author: Cameron Cobb
Last updated: 7/8/2019
Email: Cameron@CCCreno.com

Purpose: 
This program is to be used on a Raspberry Pi to take sensor data from sensors, compute calculations, 
then add the data into a .txt file. At the end of the day at exactly 2:01, the .txt file will be uploaded to Dropbox.
The program will also send the data over to a webserver to desplay date in realtime. That will be implemented soon.

TODO: 
- If file does not upload, try again in 5 min
- If disconnect, restart
- 

DONE:
- Implement MQTT
- make raspberry pi autoboot to script 
- Make sure to add total_encoder_distance() in the f.write in log_data() again
- upload files to dropbox MAKE SURE THERE IS A RASPBERRY PI PATH
- Make sure that if the encoder distance difference is less than 2 feet it is considered "down time" 
- Get multiproccessing to work properly -_-
- Put timestamp when there is an error
- Added to see if the current day was in the desired workday so the script wouldn't log to files that are not in a workday as well as upload to Dropbox when it is not needed.
'''

#Standard Imports
from datetime import datetime, timedelta, time
from multiprocessing import Process, Value
import time as t
import os
import sys

#3rd party imports
import schedule
import requests 
import dropbox
import credentials
from dropbox.files import WriteMode
from dropbox.exceptions import ApiError, AuthError
import paho.mqtt.publish as publish

if os.name == 'nt':
    print("Not importing spidev and RPI.GPIO. These libraries only work on Rasp Pi")

else:
    try:
        #The bottom 2 libraries will only work on Rasp Pi.
        import spidev
        import RPi.GPIO as GPIO

    except ImportError:
        print("Problem importing spidev and or RPI.GPIO. Make sure you are on Raspberry Pi and spidev is installed")

# Token for Dropbox
TOKEN = credentials.credentials['token'] #token is hidden in a different file so you cannot see it ;)

BROKER = credentials.credentials['broker']

uid = 2 #unique id number. CHANGE IF THIS IS A NEW MACHINE.

# The string variable for the identifying machine. This is used 
# to make the filepaths and should be "Machine1", "Machine2", "Machine3"...
# NO machine should have the same identity. 

machineID = "machine" + str(uid)

topicRoot = "data/" + machineID

# Days in which to log to file and upload. Add or remove days accordingly
workingDay = ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday')


# File directory for windows systems only. If running on linux based opperating system change file paths accordingly
if os.name == 'nt':
    pathdir = "C:\\Users\\Cameron\\Desktop\\" + machineID   #put the path on where you want to put the files
    errordir = pathdir + "\\error-log"              #error log directory
    sensorDatadir = pathdir + "\\sensor-readings"   #sensor readings directory

else:
    # Linux/Raspberry pi directories
    pathdir = "/home/pi/Desktop/" + machineID           #put the path on where you want to put the files
    errordir = pathdir + "/error-log"               #error log directory
    sensorDatadir = pathdir + "/sensor-readings"    #sensor readings directory

# Global variables for logging to file
detected = False #state of the laser sensor detection
count = Value('i', 0) #count of the laser sensor
encoder = 0 #set to 0 until instance is made
lastEncoderCount = 0 # Temporary encoder value to calculate the last encoder count
totalShiftTime = 0 #total shift time is set to 0
totalOperationTime = 0 #total opperation time is set to 0
downTimeState = False #boolean state of the machine. If "True" then the machine is in down time
shiftTimeTime = timedelta(minutes=0) #Actual shift time and is in the time format (e.g. 0:03:00) 
operationTimeTime = timedelta(minutes=0) #Same as above but for operation time...
downTime = 0 #yeah...
state = "OFF"

# Pin variables, both on GPIO not "Board" config
ORANGE_LED_PIN = 20
GREEN_LED_PIN = 21 
LASER_PIN = 14 

'''This is a class for the LS7366R rotary encoder buffer. I could've made this into a separate file to make the script
look cleaner but I chose not to just to make things a little easier when transfering the new script. '''

class LS7366R():

    #-------------------------------------------
    # Constants

    #   Commands
    CLEAR_COUNTER = 0x20
    CLEAR_STATUS = 0x30
    READ_COUNTER = 0x60
    READ_STATUS = 0x70
    WRITE_MODE0 = 0x88
    WRITE_MODE1 = 0x90

    #   Modes

    #May need to be change "QUADRATURE_COUNT_MODE" line depending on the quadrature count mode... look at datasheet.
    #These values are in HEX (base 16) whereas the data sheet displays them in binary.
    #Datasheet can be found here: https://www.lsicsi.com/pdfs/Data_Sheets/LS7366R.pdf

    #0x00: non-quadrature count mode. (A = clock, B = direction).
    #0x01: x1 quadrature count mode (one count per quadrature cycle).
    #0x02: x2 quadrature count mode (two counts per quadrature cycle).
    #0x03: x4 quadrature count mode (four counts per quadrature cycle).

    QUADRATURE_COUNT_MODE = 0x00


    FOURBYTE_COUNTER = 0x00
    THREEBYTE_COUNTER = 0x01
    TWOBYTE_COUNTER = 0x02
    ONEBYTE_COUNTER = 0x03

    BYTE_MODE = [ONEBYTE_COUNTER, TWOBYTE_COUNTER, THREEBYTE_COUNTER, FOURBYTE_COUNTER]

    #   Values
    max_val = 4294967295
    
    # Global Variables

    counterSize = 4 #Default 4
    
    #----------------------------------------------
    # Constructor

    def __init__(self, CSX, CLK, BTMD):
        self.counterSize = BTMD #Sets the byte mode that will be used


        self.spi = spidev.SpiDev() #Initialize object
        self.spi.open(0, CSX) #Which CS line will be used
        self.spi.max_speed_hz = CLK #Speed of clk (modifies speed transaction) 

        #Init the Encoder
        print('Clearing Encoder CS%s\'s Count...\t' % (str(CSX)), self.clearCounter())
        print('Clearing Encoder CS%s\'s Status..\t' % (str(CSX)), self.clearStatus())

        self.spi.xfer2([self.WRITE_MODE0, self.QUADRATURE_COUNT_MODE])
        
        t.sleep(.1) #Rest
        
        self.spi.xfer2([self.WRITE_MODE1, self.BYTE_MODE[self.counterSize-1]])

    def close(self):
        print('\nThanks for using me! :)')
        self.spi.close()

    def clearCounter(self):
        self.spi.xfer2([self.CLEAR_COUNTER])

        return '[DONE]'

    def clearStatus(self):
        self.spi.xfer2([self.CLEAR_STATUS])

        return '[DONE]'

    def readCounter(self):
        readTransaction = [self.READ_COUNTER]

        for i in range(self.counterSize):
            readTransaction.append(0)
            
        data = self.spi.xfer2(readTransaction)

        EncoderCount = 0
        for i in range(self.counterSize):
            EncoderCount = (EncoderCount << 8) + data[i+1]

        if data[1] != 255:    
            return EncoderCount
        else:
            return EncoderCount - (self.max_val+1)  
        
    def readStatus(self):
        data = self.spi.xfer2([self.READ_STATUS, 0xFF])
        
        return data[1]

#Initial setup function
def setup():

    global encoder, LASER_PIN

    if len(TOKEN) == 0:
        sys.exit("ERROR: No access token... Input an access token for Dropbox")

    try:
        url = "https://www.google.com"
        requests.get(url)
        status = "Connected to WIFI!"

    except:
        status = "Not connected to Internet. Check WIFI connection!"
    print(status)

    encoder = LS7366R(0, 1000000, 4) #Creating instance of encoder, is 1st parameter is CE0 or CE1, 2nd CLK is the speed, 3rd is BTMD which is the bytemode 1-4 the resolution of your counter

    GPIO.setmode(GPIO.BCM) # Refering to the GPIO pins, NOT board pins

    GPIO.setup(LASER_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP) #Sets to an input pull-up resistor

    #Initilizes the orange LED pin used for logging status
    GPIO.setup(ORANGE_LED_PIN, GPIO.OUT) 
    GPIO.output(ORANGE_LED_PIN, GPIO.LOW) #turns off Orange LED to signify that the logging is not in progress

    #Turning LED on signifying "Good to go"
    GPIO.setup(GREEN_LED_PIN, GPIO.OUT)
    GPIO.output(GREEN_LED_PIN, GPIO.HIGH)
    print("Ready!")

#Reads the laser sensor
def read_laser(c):

    global detected, LASER_PIN

    while True:
        sensorVal = GPIO.input(LASER_PIN)
        
        if((sensorVal == 0) and (detected == False)):
            #print("High")
            c.value += 1
            #print(c.value) #prints the count of the knife
            detected = True
            
        elif((sensorVal == 1) and (detected == True)):
            #print("Low")
            detected = False

def check_in_interval(startTime, endTime, nowTime): # Check if time is within an interval
    #nowTime = nowTime or datetime.utcnow().time()
    if startTime < endTime:
        return nowTime >= startTime and nowTime <= endTime
    else: #Over midnight
        return nowTime >= startTime or nowTime <= endTime

def is_working_day(): # Function to check if the current day is within the "workingDay" tuple and returns True or False
    if datetime.now().strftime("%A") in workingDay:
        return True
    else:
        return False

def log_error(): # This function is to log an error to a file. This is usually called after an exception
    import traceback
    print("******************************************\n"
          "*         THERE WAS AN ERROR!!!          *\n"
          "******************************************")
    try:
        if not os.path.exists(errordir):
            os.makedirs(errordir)
        else:
            print("Directory already exists -- NOT AN ERROR")
    except:
        print("Error making directory")
    
    if os.name == 'nt':
        #Windows
        filename = errordir + "\\errorlog " + str(datetime.now().strftime("%m-%d-%y")) + ".txt"
    else:
        #Linux/Raspberry pi
        filename = errordir + "/errorlog " + str(datetime.now().strftime("%m-%d-%y")) + ".txt" 

    with open(filename, 'a') as f:
        f.write("\n\n" + ('*' * 40) + "\n" + "Timestamp: " + str(datetime.now()) + "\n"+ traceback.format_exc())
        f.close
    
    print("Error has been logged, this was the error: \n\n")
    print(traceback.format_exc() + "\n\n")


def log_data(): #Logs the necessary data to the file

    global downTimeState, lastEncoderCount, totalShiftTime, totalOperationTime, shiftTimeTime, operationTimeTime, downTime, state, uid

    print(datetime.now()) # Not necessary. Just wanted to include this to help debug

    nowdate = datetime.now().strftime("%m-%d-%Y")
    nowtime = datetime.now().strftime("%H:%M:%S")

    precision = 2 #how many decimals to round

    encodercount = encoder.readCounter() #gets the encoder count and stores it in encodercount variable

    total_encoder_distance = encodercount/1000 #The encoder has 500 pulses per revolution and circumference is 6 inches so dividing by 1000 will give total distance in feet

    encoder_difference = get_encoder_difference(total_encoder_distance) #gets the difference of the encoder

    knife_count = count.value #current count of the knife

    CPM_BY_OPERATION = cpm_by_operation_time()

    CPM_BY_SHIFT = cpm_by_shift_time()

    try:
        if check_in_interval(time(6, 00), time(14, 00), datetime.now().time()) and is_working_day(): #Only logs the data between 6:00 AM - 2:00 PM

            GPIO.output(ORANGE_LED_PIN, GPIO.HIGH) #Turns on Orange LED pin to signify that logging in progress

            #Makes the directory if it does not exist
            try:
                if not os.path.exists(sensorDatadir):
                    os.makedirs(sensorDatadir)
                else:
                    print("Directory already exists -- NOT AN ERROR")
            except:
                print("Error making directory")

            
            #filename is the path and current date with .txt
            #Windows
            if os.name == 'nt':
                filename = sensorDatadir + "\\" + str(datetime.now().strftime("%m-%d-%y")) + ".txt"
            
            else:
                #Linux/Raspberry pi
                filename = sensorDatadir + "/" + str(datetime.now().strftime("%m-%d-%y")) + ".txt" 

            with open(filename, 'a') as f:
                
                if os.stat(filename).st_size == 0:
                    #Prints the headers for the file
                    f.write("Date,Time,Total Cycle Count,Cycles Per Minute By Operation Time,"
                            "Cycles Per Minute By Shift Time,Encoder Count (ft),Down Time,"
                            "Operation Time (shift time-downtime),Shift time,"
                            "Total Shift Time (minutes),Total Operation Time (minutes)\n") 

                #This is how data will be logged to .txt file
                f.write(nowdate + ',' + nowtime + ',' + str(knife_count) + ',' + str(round(CPM_BY_OPERATION, precision)) + ',' 
                        + str(round(CPM_BY_SHIFT, precision)) + ',' + str(round(total_encoder_distance, precision)) + ',' + str(timedelta(minutes = downTime)) + ','
                        + str(operationTimeTime) + ',' + str(shiftTimeTime) + ',' + str(totalShiftTime) + ','
                        + str(totalOperationTime) + '\n') #line that writes to file. MAKE SURE YOU PUT total_encoder_distance() again!!

                f.close

                print("Data has been logged!")

                totalShiftTime += 1 #increments total shift time by 1 because being logged by every 1 minute
                shiftTimeTime += timedelta(minutes=1) #increments total shift time by 1 minute. This is the actual time variable!!

                if encoder_difference > 30: #if the difference in the encoder is greater than 30 feet then the machine is running 
                    totalOperationTime += 1
                    operationTimeTime += timedelta(minutes=1) #increments total operating time by 1 minute IF THE ENCODER DIFFERENCE IS NOT 0 (meaning the encoder has moved since last read). This is the actual time variable!!
                    downTimeState = False
                    state = "RUNNING"

                else: #if the difference in the encoder is less than 30 then it is considered "down time".
                    downTimeState = True
                    downTime += 1 #increments total down time by 1 minute IF THE ENCODER DIFFERENCE IS 0 (meaning the encoder hasn't moved since last read). This is the actual time variable!
                    state = "DOWN"
                print("Knife count: " + str(knife_count) + " Encoder distance: " + str(round(total_encoder_distance, precision))) #prints to terminal for debugging

                

        else:
            print("Time was not in interval or is not in workingDay so data was not logged")
            GPIO.output(ORANGE_LED_PIN, GPIO.LOW) #turns off Orange LED to signify that the logging is not in progress
            state = "OFF"

        #This is to format the data being sent over MQTT
        data = (str(uid)+"$"+state+"$"+str(datetime.now())+"$"+str(knife_count)+"$"+str(round(CPM_BY_OPERATION, precision))+
                "$"+str(round(CPM_BY_SHIFT, precision))+"$"+str(round(total_encoder_distance, precision))+"$"+str(downTime)+
                "$"+str(totalShiftTime)+"$"+str(totalOperationTime))
        try:
            publish.single(topicRoot, data, hostname=BROKER)
            print("Data has been sent via MQTT")
        except:
            print("Data was NOT sent")

    except:
        log_error()

def delete_files(): #function that deletes the oldest files in on the system

    days = 365 #The amount of days/files to keep on the system. Files are made by the day.

    try:
        #Deletes old sensor-data files
        while len([name for name in os.listdir(sensorDatadir)]) > days: #Keep the most recent days files. Can be changed by changing the "days" variable
            list_of_files = os.listdir(sensorDatadir)

            #Windows
            if os.name == 'nt':
                full_path = [sensorDatadir + "\\{0}".format(x) for x in list_of_files]

            #Linux/Raspberry pi
            else:
                full_path = [sensorDatadir + "/{0}".format(x) for x in list_of_files]

            oldest_file = min(full_path, key=os.path.getctime)
            os.remove(oldest_file) #Deletes the oldest files

        print("Old sensor files have been removed")

        #Deletes old error-log files
        while len([name for name in os.listdir(errordir)]) > days: #Keep the most recent days files. Can be changed by changing the "days" variable
            list_of_files = os.listdir(errordir)

            #Windows
            if os.name == 'nt':
                full_path = [errordir + "\\{0}".format(x) for x in list_of_files]

            #Linux/Raspberry pi
            else:
                full_path = [errordir + "/{0}".format(x) for x in list_of_files]

            
            oldest_file = min(full_path, key=os.path.getctime)
            os.remove(oldest_file) #Deletes the oldest files

        print("Old error-log files have been removed")

    except:
        log_error()

def reset_values():  #Function for resetting the values back to 0

    if is_working_day():  #Not really necessary but I just wanted to add it

        global count, lastEncoderCount, totalShiftTime, totalOperationTime, shiftTimeTime, operationTimeTime, downTime

        with count.get_lock(): #because the laser counter is running on a separate proccess, this is the method to reset it
            while count.value != 0:
                count.value = 0 #initilizes back to 0
                print("Laser count has been reset to 0")

        encoder.clearCounter() #encoder count set to 0
        print("Current encoder count has been reset to 0")
        lastEncoderCount = 0
        print("Last encoder count has been reset to 0")
        totalShiftTime = 0
        totalOperationTime = 0
        shiftTimeTime = timedelta(minutes=0) #resets actual shift time 
        operationTimeTime = timedelta(minutes=0) #resets actual operation time
        downTime = 0 #resets actual down time time

    else:
        print("Not a working day so values were not reset. Doesn't matter because they will be reset before next shift.")

def get_encoder_difference(total_encoder_distance): 

    global lastEncoderCount

    dif = total_encoder_distance - lastEncoderCount
    lastEncoderCount = total_encoder_distance
    return dif

def cpm_by_operation_time(): #cycles per minute by operation time

    try:
        return count.value/totalOperationTime

    except ZeroDivisionError:
        return 0

    except:
        log_error()
        return "ERROR" #returns "ERROR" and logs it to file if there is an error 

def cpm_by_shift_time(): #cycles per minute by shift time

    try: 
        return count.value/totalShiftTime

    except ZeroDivisionError:
        return 0

    except:
        log_error()
        return "ERROR" #returns "ERROR" and logs it to file if there is an error 

def upload_files_to_dropbox():

    if is_working_day(): #checks if the current day is within the workingDay tuple. If is_working_day returns True then it will perform what is below.

        #Local paths on system to upload to Dropbox (Windows)
        if os.name == 'nt':
            localFile = sensorDatadir + "\\" + str(datetime.now().strftime("%m-%d-%y")) + '.txt'
            errorFile = errordir + "\\errorlog " + str(datetime.now().strftime("%m-%d-%y")) + ".txt"

        #Local paths on system to upload to Dropbox (Linux/raspberry pi)
        else:
            localFile = sensorDatadir + "/" + str(datetime.now().strftime("%m-%d-%y")) + ".txt"
            errorFile = errordir + "/errorlog " + str(datetime.now().strftime("%m-%d-%y")) + ".txt" 

        #Paths on Dropbox to upload the LOCALFILE and errorFile variables
        backupPath = '/' + machineID + '/sensor-readings/' + str(datetime.now().strftime("%m-%d-%y")) + '.txt' 
        errorPath = '/' + machineID + '/error-log/error ' + str(datetime.now().strftime("%m-%d-%y")) + '.txt'

        try:
            print("Creating a Dropbox object...")
            dbx = dropbox.Dropbox(TOKEN, max_retries_on_error=4)
        
            # Check that the access token is valid
            try:
                dbx.users_get_current_account()

            except AuthError:
                sys.exit("ERROR: Invalid access token; try re-generating an "
                         "access token from the app console on the web.")

            except ApiError:
                print("ApiError, check code...")

            except:
                print("Error getting users account. Make sure device is connected to the internet.")

            try:
                with open(localFile, 'rb') as f:
                    # We use WriteMode=overwrite to make sure that the settings in the file
                    # are changed on upload
                    print("Uploading " + localFile + " to Dropbox as " + backupPath + "...")
                    try:
                        dbx.files_upload(f.read(), backupPath, mode=WriteMode('add'))
                        print("Sensor readings upload were successful!")

                    except ApiError as err:
                        # This checks for the specific error where a user doesn't have
                        # enough Dropbox space quota to upload this file

                        if (err.error.is_path() and
                                err.error.get_path().reason.is_insufficient_space()):
                            sys.exit("ERROR: Cannot back up; insufficient space.")

                        elif err.user_message_text:
                            print(err.user_message_text)
                            sys.exit()

                        else:
                            print("Error: File most likely already exists therefore didn't upload. This was the error:\n\n" + err)

            except FileNotFoundError:
                print("ERROR: Sensor data file could not be found!")
                
            try:
                with open(errorFile, 'rb') as f:
                # We use WriteMode=overwrite to make sure that the settings in the file
                # are changed on upload
                    print("Uploading " + errorFile + " to Dropbox as " + errorPath + "...")
                    try:
                        dbx.files_upload(f.read(), errorPath, mode=WriteMode('add'))
                        print("Error log upload was successful!")

                        delete_files() #deletes old files if the upload was successful

                    except ApiError as err:
                        # This checks for the specific error where a user doesn't have
                        # enough Dropbox space quota to upload this file
                        if (err.error.is_path() and
                                err.error.get_path().reason.is_insufficient_space()):
                            sys.exit("ERROR: Cannot back up; insufficient space.")

                        elif err.user_message_text:
                            print(err.user_message_text)
                            sys.exit()

                        else:
                            print("Error: File most likely already exists therefore didn't upload. This was the error:\n\n" + err)

            except FileNotFoundError:
                print("ERROR: Error log file could not be found!")
        
        except:
            print("An error occured while trying to upload files. Connection may have been lost. Check internet connection.")

#NOTE: The .every().day parts will execute everyday... but there are "if" statements within the functions that
# actually determine if the rest of the function will be ran or not.

schedule.every().day.at('6:00').do(reset_values) #reset all values at 6:00 AM

STEP = 1  # every x minutes
for minute in range(0, 60, STEP):
    time_t = ":{minute:02d}".format(minute=minute)
    schedule.every().hour.at(time_t).do(log_data)
    
schedule.every().day.at('14:01').do(upload_files_to_dropbox) #upload file to Dropbox at 2:01 PM

def main():
    try:
        setup() #initial setup function. 
        process_1 = Process(target=read_laser, args=(count,)) #This is a multiproccessing thread so the Rasp Pi will count the laser while it does everything else. 
        process_1.start()
        while True:
            schedule.run_pending() #This is needed for the schedule to work
            t.sleep(1)

    except KeyboardInterrupt:
        GPIO.cleanup() #cleans up GPIO pins. This is necessary or else it will give issues.
        process_1.terminate() #terminates the process for reading the laser

if __name__ == "__main__":
    main()
