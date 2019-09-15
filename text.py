'''
Purpose: This is a test function to upload a file to Dropbox. If
there is a connection error, it will try again 10 seconds later.

'''
import dropbox
from dropbox.files import WriteMode
from dropbox.exceptions import ApiError, AuthError
import sys
import time
import credentials
import schedule
import multiprocessing

TOKEN = credentials.credentials['token']

dbx = dropbox.Dropbox(TOKEN)

uploaded = False

#print(dbx.users_get_current_account())

backupPath =  '/machine2' + '/sensor-readings/' + 'test1.txt'
localFile = "test1.txt"

def hello():
    print("hello world, I am running")

def upload_files():
    global uploaded
    
    max_tries = 5
    total_tries = 0

    while not uploaded:
        
        try:
            with open(localFile, 'rb') as f:
                # We use WriteMode=overwrite to make sure that the settings in the file
                # are changed on upload
                print("Uploading " + localFile + " to Dropbox as " + backupPath + "...")
                try:
                    dbx.files_upload(f.read(), backupPath, mode=WriteMode('add'))
                    print("Sensor readings upload were successful!")
                    uploaded = True

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

        except:
            total_tries += 1
            print("connection error, trying in 30 seconds")
            print("Attempt {}/{}".format(total_tries, max_tries))
            if total_tries == max_tries:
                print("Reached total number of tries")
                break
            time.sleep(30)

def setUploadFunction():
    p1 = multiprocessing.Process(target=upload_files)
    p1.start()
    p1.join() 

STEP = 1  # every x minutes
for minute in range(0, 60, STEP):
    time_t = ":{minute:02d}".format(minute=minute)
    schedule.every().hour.at(time_t).do(hello)

#Unsure if this will block the main thread becasue of "time.sleep(10)"
schedule.every().day.at('17:06').do(upload_files) #upload file to Dropbox at 2:01 PM


while True:
    schedule.run_continuously()
    time.sleep(1)


