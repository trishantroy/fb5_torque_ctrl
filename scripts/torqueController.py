#!/usr/bin/env python
import rospy
import csv
import time
import struct
from fb5_torque_ctrl.msg import encoderData
from fb5_torque_ctrl.msg import PwmInput
from geometry_msgs.msg import TransformStamped
#from geometry_msgs import TransformStamped
#from geometry_msgs import Transform
import math

#Defining as global variable for loging
pwmInput=PwmInput()
codeStartTime=0
#The experimental data is added to the data.csv file
head=['time','interval','encR','encL','wR','wL','wdotR','wdotL','wddotR','wddotL','pwmR','pwmL']
with open('data.csv','w') as myfile:
	writer=csv.writer(myfile)
	writer.writerow(head)
encRPrev=0
encLPrev=0
wRprev=0
wLprev=0
wdotRprev=0
wdotLprev=0

N_bots=4
BotNumber=0  #This will be set correctly (0,1,2...N_bots) so that the bot know which location data is its own. 

bot_loc=np.empty((2,N_bots));

def callbackVICON(data, arg):
	global bot_loc
	global BotNumber
	bot_index=arg
	#Temporary shiz
	bot_loc[:,bot_index]=(data.transform.translation.x,data.transform.translation.y);
	if arg==BotNumber:
		rospy.loginfo("The bots are located at:", bot_loc)


def callback(data):
	global encRPrev
	global encLPrev
	global wRprev
	global wLprev
	global wdotRprev
	global wdotLprev
	global pwmInput
	global codeStartTime
	#rospy.loginfo(rospy.get_caller_id''() + "I heard %s", data)
	#['interval','encR','encL','wR','wL','wdotR','wdotL','wddotR','wddotL']
	#30 counts is one rotation of the wheel i.e. 2*pi radian
	duration=0.04 #25 Hz
	wR=(data.encoderR-encRPrev)*2*math.pi/30/duration
        wL=(data.encoderL-encLPrev)*2*math.pi/30/duration
        wdotR=(wR-wRprev)/duration
        wdotL=(wL-wLprev)/duration
        wddotR=(wdotR-wdotRprev)/duration
        wddotL=(wdotL-wdotLprev)/duration
	encRPrev=data.encoderR
	encLPrev=data.encoderL
	wRprev=wR
	wLprev=wL
	wdotRprev=wdotR
	wdotLprev=wdotL
        logTime=rospy.get_time()-codeStartTime
        row=[logTime,data.interval,data.encoderR,data.encoderL,wR,wL,wdotR,wdotL,wddotR,wddotL,pwmInput.rightInput,pwmInput.leftInput]
        with open('data.csv','ab') as myfile:
                writer=csv.writer(myfile)
                writer.writerow(row)
	print(rospy.get_time()-codeStartTime-logTime) #Just to see how long this logging takes

#def callbackVICON(data):

def torqueController():
	global pwmInput
	global codeStartTime
    rospy.Subscriber('encoderData', encoderData, callback)

    #VICON data subscriber. Change the name to the required name here. The bot this is running on is to be placed last.
	rospy.Subscriber("/vicon/vijeth_1/vijeth_1", TransformStamped, callbackVICON,1)
	rospy.Subscriber("/vicon/vijeth_2/vijeth_2", TransformStamped, callbackVICON,2)
	rospy.Subscriber("/vicon/vijeth_3/vijeth_3", TransformStamped, callbackVICON,3)
	rospy.Subscriber("/vicon/vijeth_0/vijeth_0", TransformStamped, callbackVICON,0)

	pub_PWM=rospy.Publisher('pwmCmd',PwmInput,queue_size=10)
    rospy.init_node('torqueController',anonymous=True)
    #The torque controller outputs commands at only 10Hz.
	#The encoder data is still at 25 Hz as determined by the publisher in the other file
	codeStartTime=rospy.get_time()
	rate = rospy.Rate(10)
	timeSinceInput=rospy.get_time()
        timeBetInputs=10
        while not rospy.is_shutdown():
		i=0
		while i<2:
			pwmInput.rightInput=200*i
			pwmInput.leftInput=200*i
                        #rospy.loginfo(pwmInput)
                        pub_PWM.publish(pwmInput)
			if((rospy.get_time()-timeSinceInput)>=timeBetInputs):
				timeSinceInput=rospy.get_time()
                                i=i+1
			rate.sleep()
if __name__ == '__main__':
    try:
        torqueController()
    except rospy.ROSInterruptException:
        pass



    
 
