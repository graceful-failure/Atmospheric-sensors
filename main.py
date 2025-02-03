import urequests as requests
import usocket as socket
import network
import scd4x
import json
import machine

from machine import SoftI2C
from machine import reset

from math import log2
from time import sleep


i2c = SoftI2C(sda=Pin(21), scl=Pin(22), freq=100000, timeout = 50000)
station = network.WLAN(network.STA_IF)

transistor = Pin(4, Pin.OUT) #V14 set up Port pin 4 (Labelled D12 on chip) as the transistor switch
transistor.on() #Switch the transistor on at startup

ssid = "XXXX"
password = "XXXX"

occupied_channels = [] #a list of the numbers of channels that have a sensor on
failure_count = [] #a list of the channel codes of sensors that have failed to give a result


def scan_sensors():
    for channel in range (8):
        i2c.writeto(0x70, (pow(2,channel)).to_bytes(1,1))#0x70 is the address of the multiplexer. The rest creates the byte channel addresses
        print("Scan channel " + str(channel))
        i2c.scan()
        for device_address in i2c.scan():
            print(hex(device_address))
            if device_address != 0x70:
                occupied_channels.append(channel)
    print("Occupied channels ",occupied_channels)
    return occupied_channels


def start_sensors(occupied_channels):
    for channel in occupied_channels:
        i2c.writeto(0x70, (pow(2,channel)).to_bytes(1,1))
        try:
            print("Sensor on channel {} serial number {} checking self calibration".format(channel, [hex(i) for i in sensor.serial_number]))
        except:
            print("Whoops. Not working, restart!")
            restart()
        
        if not sensor.self_calibration_enabled:
            print("Enabling self calibration")
            sensor.self_calibration_enabled = True
            sensor.persist_settings()
            sensor.reinit()
            print("Testing saved self calibration setting")
            print(sensor.self_calibration_enabled)
        
        #V14 addition to get/set ASC interval and increase it 4x as we're polling sensors 4x in 5 mins rather than 1x
        print ("Checking ASC interval")
        print(sensor.self_calibration_standard_period)
        
        if sensor.self_calibration_standard_period != 624:
            print("Updating ASC interval to 624 hours")
            sensor.self_calibration_standard_period = 624 #624 is 4x standard interval of 156hours. This is because we are polling the sensor 4x more often.
            sensor.persist_settings()
            sensor.reinit()
            print("Testing ASC interval has changed to 624 hours. ASC interval is")
            print(sensor.self_calibration_standard_period)
        else:
            print("ASC was already set to 624 hours. Continuing.")
            
        #print("Testing sensor")
        #sensor.self_test()
        print("Testing measurement")
        sensor.single_shot_measurement()
        print("Test result from channel {} temperature {} degrees humidity {} % CO2 {} PPM".format(channel, sensor.temperature, sensor.relative_humidity, sensor.CO2))
        sensor.power_down()
        
def read_sensors(occupied_channels):
    output = {}
    output["Failures"] = []
    for channel in occupied_channels:
        output[channel]={}
        output[channel]["temperature"] = []
        output[channel]["humidity"] = []
        output[channel]["CO2"] = []
    
    transistor.on() #V14 Switch on power to the multiplexer
        
    for loop in range(0, 4): #loop 4 times
        for channel in occupied_channels:
            print("Querying channel: ", channel)
            i2c.writeto(0x70, (pow(2,channel)).to_bytes(1,1))
            sensor.wake_up() #V12
            sensor.single_shot_measurement()
            
            try:
                output[channel]["temperature"].append(sensor.temperature)
                output[channel]["humidity"].append(sensor.relative_humidity)
                output[channel]["CO2"].append(sensor.CO2)
            except:
                print("Sensor on channel {} unready on pass {}".format(channel, loop + 1))
            sensor.power_down() #V12
    
    transistor.off() #V14 Switch on power to the multiplexer
    print("transistor off")
    for channel in occupied_channels:
        if len(output[channel]["temperature"]) + len(output[channel]["humidity"]) + len(output[channel]["CO2"]) != 12:
            output["Failures"].append(channel)
        output_add(output, (str(channel) + "_Temp"), smoothaverage(output[channel]["temperature"])) 
        output_add(output, (str(channel) + "_Humidity"), smoothaverage(output[channel]["humidity"])) 
        output_add(output, (str(channel) + "_CO2"), smoothaverage(output[channel]["CO2"])) 
        del output[channel]#delete the unaveraged sensor data which lies in a dictionary key called with the channel name 
        
        
    deltatemp = []
    deltahumidity = []
    deltaCO2 = []
    for channel in occupied_channels:
        deltatemp.append(output[(str(channel) + "_Temp")][0]["value"])
        deltahumidity.append(output[(str(channel) + "_Humidity")][0]["value"])
        deltaCO2.append(output[(str(channel) + "_CO2")][0]["value"])
        

    deltatemp = max(deltatemp) - min(deltatemp)
    output_add(output,"Delta_Temp",deltatemp)
    deltahumidity = max(deltahumidity) - min(deltahumidity)
    output_add(output, "Delta_Humidity",deltahumidity)
    deltaCO2 = max(deltaCO2) - min(deltaCO2)
    output_add(output, "Delta_CO2",deltaCO2)
    
    return output 

def restart():
    print("Rebooting")
    reset()

def connect_internet():
    station.active(True)
    station.connect(ssid, password)
    while station.isconnected() == False:
      pass

def disconnect_internet():
    station.disconnect()
    station.active(False) #V14- attempt to save power by switching off

def output_add(dictionary, category, value):
    if category not in dictionary: dictionary[category] = []
    dictionary[category].append({"value":value})

def smoothaverage(somevalues):
    if type(somevalues) is set: somevalues = list(somevalues)
    if type(somevalues) is dict: somevalues = list(somevalues.values())
    """maybe add in tuple, int and string handling at some point? Not that I use them much"""
    if len(somevalues) < 4: #check if 0 is the first number right?
        return (sum(somevalues)/len(somevalues)) #error handling. Accept a crappy result rather than fail completely.
    else:
        somevalues.sort()
        somevalues.pop(0)
        somevalues.pop(-1)
        return (sum(somevalues)/len(somevalues))

"""Main routine starts here"""

occupied_channels = scan_sensors()
sensor = scd4x.SCD4X(i2c)
start_sensors(occupied_channels)

while True:
    results = {}
    results["data"] = {}
    results["data"] = read_sensors(occupied_channels)
        
    """check for errors in results. Each channel should have three sensor results."""
    if results["data"]["Failures"]: #Skips all this bit if there's no content in Failures
        print("Failures in this cycle: ", results["data"]["Failures"])
        failure_count.extend(results["data"]["Failures"])
        for channel in occupied_channels:
            print("Count of channel {} total failures is {}".format(channel, failure_count.count(channel)))
            output_add(results["data"],(str(channel) + "_failures"), failure_count.count(channel))
        del results["data"]["Failures"]#Deletes Failures because all the data is transferred to individual channel failure counts
    
    print("Results output.")
    print(json.dumps(results)) # testing lines use json dumps to see what results looks like
    print("Results ends")
    
    
    try:
        connect_internet()
        headers = {'api-key': 'XXXX'}
        requests.post("http://iotplotter.com/api/v2/feed/XXXX", headers=headers, data=json.dumps(results))
        disconnect_internet()
        
    except:
        print("Upload failure, disconnecting")
        disconnect_internet()
                
    for channel in occupied_channels: #Moved this down here so it does any pending uploads first before restarting.
        if failure_count.count(channel) > 9 :
                print("Restarting")
                restart()
    sleep(300)
