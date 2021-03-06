#!/usr/bin/env python
import rospy
import csv
import time
import math
import struct
import numpy as np
from fb5_torque_ctrl.msg import encoderData
from fb5_torque_ctrl.msg import PwmInput
from fb5_torque_ctrl.msg import botStatus
from geometry_msgs.msg import TransformStamped
from scipy.stats import multivariate_normal
from numpy.linalg import inv

#Computing weighted Voronoi partitions

#First we create the the grid area in which we want the search to be performed.
#The origin of the the grid is set at the orgin of Vicon system. We take a grid
#of 5m by 5m as a few VICON cameras are broken and coverage is restricted.

origin = np.array([0.0,0.0])

grid_Size = 5.0 #in meters
grid_Res = 0.02 #in meters. Each square is 2 cm width.

N_Grid_Points = int(grid_Size/grid_Res) #We gonna assume square grids. Else even the Voronoi partition function has to change.

x_grid=np.arange(-grid_Size/2+origin[0], grid_Size/2+origin[0], grid_Res)+grid_Res/2 #+grid_Res/2 gives the centroid
y_grid=np.arange(-grid_Size/2+origin[1], grid_Size/2+origin[1], grid_Res)+grid_Res/2

X_grid, Y_grid = np.meshgrid(x_grid,y_grid)

#pos_grid is a three 250x250 matrix with each point holding the coordinate of the centroid of a given grid square.
#eg. pos_grid[0,0,:] will give the 1st squares centroid as [-2.49,-2.49]
pos_grid = np.empty(X_grid.shape + (2,))
pos_grid[:, :, 0] = X_grid; pos_grid[:, :, 1] = Y_grid


#Creating the phenonmenon (through definition of the basis functions) to be sensed in the environment.
mu0 = np.array([5*0.25, 5*0.25]) 	#The basis is formed by taking two gaussians on each diagnol and a constant function. 
mu1 = np.array([-5*0.25, -5*0.25])
mu2 = np.array([5*0.25, -5*0.25])
mu3 = np.array([-5*0.25, 5*0.25])
Sigma = np.array([[150.0/N_Grid_Points, 0],[0, 150.0/N_Grid_Points]])

rv0 = multivariate_normal(mu0, Sigma)
rv1 = multivariate_normal(mu1, Sigma)
rv2 = multivariate_normal(mu2, Sigma)
rv3 = multivariate_normal(mu3, Sigma)

#Adding all the basis ventors in 1 variable.
K=np.dstack((rv0.pdf(pos_grid), rv1.pdf(pos_grid),rv2.pdf(pos_grid),rv3.pdf(pos_grid),np.ones(pos_grid[:,:,0].shape)))

#Randomly choosen weights to each element in the basis function.
a=np.array([1.0/2,1.0/4,1.0/3,1.0/9,1.0/5]) 	#np.array([1,1,1,1,1]). This is the actual environmental distribution of the phenomenon.
a_hat=np.array([1.0/3,1.0/3,1.0/4,1.0/8,1.0/6]) #Estimate of above.
a_min=np.array([1.0/20,1.0/20,1.0/20,1.0/20,1.0/20]) #The a_min determines the lowest value the update parameter should take.
phi=a[0]*K[:,:,0]+a[1]*K[:,:,1]+a[2]*K[:,:,2]+a[3]*K[:,:,3]+a[4]*K[:,:,4] #Environmental distribution.

#The number of bots and their locations here will be fed to the system from the master computer later.
N_bots=4
myStatus=botStatus()

botStatuses=np.array([False,False,False,False])
##############################################################################################################
#####################################Change for each bot######################################################
##############################################################################################################
BotNumber=1  #This will be set correctly (0,1,2...N_bots) so that the bot know which location data is its own.
##############################################################################################################
bot_loc=np.empty((2,N_bots));

#Defining as global variable for loging
q=np.array([[0.0],[0.0],[0.0]])
q_prev=np.array([[0.0],[0.0],[0.0]])

#Lambda and lambda parameter initialisations.
lambda_up=np.matrix(np.zeros((5,5)))
lambda_low=np.zeros(5)

#Defining the initial velocity in cotinuatuion of defining the initial state of the bot.
vel = np.array([[0.0],[0.0]])

#Defining control as global variable for logging
u = np.array([[0.0],[0.0]])
tau = np.array([[0.0],[0.0]])

#This boolean decideds whther we wish a new data to be taken when the VICON subscriber finds a new data-point.
#Look at the callbackVICON function to better understand its function.
wantData=True

#Defining the bot properties the best we know
m=1.72 #mass in kg
d=0.065 #distance from CG to axis
R=0.085	#Semi-Distance between wheels
r=0.025 #Radius of wheels

#Torque to PWM
#We will neglect the w_ddot term as well as the tau_dot terms. The former is difficult to obtain with our current encoder.
#The latter is eliminated as its computations are quite tedious.
K_wdot=0.088			#(b+RJ/Kt). This is the coefficient of w_dot
K_w=0.003
K_tau_R=18000
K_tau_L=18000

#Controller requisite gains.
alpha=1
Gamma=1
gamma=1
k1=1
k2=1

pwmInput=PwmInput()
codeStartTime=0

#The experimental data is added to the data.csv file
head=['time','interval','wR','wL','wdotR','wdotL','pwmR','pwmL','x','y','theta','vel_v','vel_w','e_x','e_y','e_theta','u_R','u_L','tau_R','tau_L']
with open('data.csv','w') as myfile:
	writer=csv.writer(myfile)
	writer.writerow(head)


#Since tf package can't be directly installed on RasPi the function below has be written.
#Note that this function cannot handle singularities. It gives ZYX rotation Euler angles in radian.
def quat2eul(qu):
	global phi
	global theta
	global psi
	#Computing Phi
	phi = math.atan2(2*(qu[0]*qu[1]+qu[2]*qu[3]),1-2*(qu[1]**2+qu[2]**2))

	#Computing Theta
	#Introducing a check to avoid numerical errors
	sinTheta=2*(qu[0]*qu[2]-qu[1]*qu[3])
	if sinTheta>=1:
		theta=math.pi/2
	else:
		theta=math.asin(sinTheta)

	#Computing Psi
	psi=math.atan2(2*(qu[0]*qu[3]+qu[2]*qu[1]),1-2*(qu[2]**2+qu[3]**2))
	return np.array([[phi],[theta],[psi]])

#Defining the nonlinear system properties.
def S(q):
	global d
	s=np.matrix([[math.cos(q[2]), -d*math.sin(q[2])],[math.sin(q[2]), d*math.cos(q[2])],[0, 1]])
	return s

def S_dot(q,q_dot):
	global d
	s_dot=np.matrix([[-math.sin(q[2])*(q_dot[2][0]), -d*math.cos(q[2])*(q_dot[2][0])],[math.cos(q[2])*(q_dot[2][0]), -d*math.sin(q[2])*(q_dot[2][0])],[0, 0]])
	return s_dot

def M(q):
	global d
	global m
	inertia=np.matrix([[m, 0, m*d*math.sin(q[2])],[0, m, -m*d*math.cos(q[2])],[m*d*math.sin(q[2]), -m*d*math.cos(q[2]), 1]])
	return inertia

def V(q,q_dot):
	global d
	global m
	coriolis=np.matrix([[0, 0, m*d*(q_dot[2])*math.cos(q[2])],[0, 0, m*d*(q_dot[2])*math.sin(q[2])],[0, 0, 0]])
	return coriolis

def B(q):
	global R
	global r
	cont_transform=1/r*np.matrix([[math.cos(q[2]), math.cos(q[2])],[math.sin(q[2]), math.sin(q[2])],[R, -R]])
	return cont_transform

#By calculation we obtain B_bar as [[1/r,1/r],[R/r -R/r]]
def B_bar(q):
	b_bar=S(q).transpose()*B(q)
	return b_bar

#By calculation we obtain M_bar as [[m 0],[0 1-md^2]].
def M_bar(q):
	return S(q).transpose()*M(q)*S(q)


#By calculation we get V_bar as a 2x2 null matrix.
def V_bar(q,q_dot):
	return S(q).transpose()*(M(q)*S_dot(q,q_dot)+V(q,q_dot)*S(q))

#Assume constant rolling friction acting opposite to the direction of motion.
F_bar = np.matrix([[0.002],[0.002]])

#Defining the required torque at each instant.
def torque(q,q_dot,v,u):
	global F_bar
	return B_bar(q).I*(M_bar(q)*np.matrix(u)+V_bar(q,q_dot)*np.matrix(v)+F_bar) #Assuming zero disturbance Tau_d

#Rotation matrix from body frame to inertial frame
def rot2body(q):
	rotmat=np.matrix([[math.cos(q[2]), math.sin(q[2]), 0],[-math.sin(q[2]), math.cos(q[2]), 0],[0, 0, 1]])
	return rotmat

#The subscriber here to take all the bot_loc data and the bots orientation.
def callbackVICON(data, args):
	global q
	global BotNumber
	global bot_loc
	global wantData
	global q_prev
	#This boolean wantData is here to restrict q from changing when we don't want it to i.e. inside our loop.
	#Also any value assigned equal to the global variable q once in the torqueController loop keeps changing it's value. I kid you not, I struggled for an entire day trying to understand it.
	#Even if it's assigned at the bottom of the loop it used to change when q was updated after q_prev assignment. (Again, not kidding.)
	#So we will treat it like a global variable and deal with it in the callback itself.
	if wantData:
		if args==BotNumber:
			q_prev[0][0]=q[0][0]
			q_prev[1][0]=q[1][0]
			q_prev[2][0]=q[2][0]

			q[0][0]=data.transform.translation.x
			q[1][0]=data.transform.translation.y
			eulerAng=quat2eul([data.transform.rotation.x, data.transform.rotation.y, data.transform.rotation.z, data.transform.rotation.w])
			#The order of rotation so happens that phi is actually psi. Hence eulerAng[0] is used.
			q[2][0]=eulerAng[0]
		bot_loc[:,args]=np.array([data.transform.translation.x,data.transform.translation.y])
		wantData=False

#This stores the the status of each bot whther it is moving or not. The voronoi computation happens only if no bots are movie. 
def callbackBotStatus(data,args):
	global botStatuses
	botStatuses[args]=data.isMoving

#Simple Euler distance
def cartesianDist(a,b):
	return math.sqrt((a[0]-b[0])**2+(a[1]-b[1])**2)

#Writing the Voronoi partition calculator function.
#This function returns the set of points on the pos_grid which lie within the bot's voronoi partition.
#Note: We will only store the indices (i,j) of the points on the mesh grid as these are easioer to work with instead of the point coordinates themselves.
def voronoi(grid, Nbots, BotNo, locations):
	VPartition=[]
	N_Grid_Points = len(grid[:,0,0])
	for i in range(N_Grid_Points):
		for j in range(N_Grid_Points):	#This iterates over all points in the domain.
			inPartition=True 			#This stays one as long as the point is closer to the botIn question than any other point.
			for N in range(Nbots):
				if N!=BotNo and inPartition:
					inPartition = inPartition and cartesianDist(grid[i,j,:],locations[:,BotNo])<cartesianDist(grid[i,j,:],locations[:,N])
			if(inPartition):
				VPartition.append(np.array([i,j]))
	return VPartition

def Lv(partition,grid,K,a_hat,grid_res):
	phi_hat=a_hat[0]*K[:,:,0]+a_hat[1]*K[:,:,1]+a_hat[2]*K[:,:,2]+a_hat[3]*K[:,:,3]+a_hat[4]*K[:,:,4]
	LV=np.array([0.0,0.0])
	dq=grid_res*grid_res
	for point in partition:
		LV=LV+phi_hat[point[0],point[1]]*grid[point[0],point[1],:]*dq #integral(q*phi(q)*dq). dq is a constant equal to area of grid square.
	return LV

def Mv(partition,K,a_hat,grid_res):
	phi_hat=a_hat[0]*K[:,:,0]+a_hat[1]*K[:,:,1]+a_hat[2]*K[:,:,2]+a_hat[3]*K[:,:,3]+a_hat[4]*K[:,:,4]
	MV=0.0
	dq=grid_res*grid_res
	for point in partition:
		MV=MV+phi_hat[point[0],point[1]]*dq
	return MV

def torqueController():
	global pwmInput
	global codeStartTime
	global q
	global q_prev
	global vel
#	global encRPrev
#	global encLPrev
#	global wRprev
#	global wLprev
#	global wdotRprev
#	global wdotLprev
#	global e
#	global u
#	global tau
#	global K_tau
#	global wR
#	global wL
#	global wdotR
#	global wdotL
	global pos_grid
	global K
	global bot_loc
	global q
	global a
	global a_hat
	global BotNumber
	global N_bots
	global myStatus
	global grid_Res
	global botStatuses

	rospy.init_node('torqueController',anonymous=True)
	#pub_PWM=rospy.Publisher('pwmCmd',PwmInput,queue_size=10)

	#####################Change for each bot###########################
	pub_myStatus=rospy.Publisher('botStatus1',botStatus,queue_size=10)
	###################################################################

	#VICON data subscriber. Change the name to the required name here.
	rospy.Subscriber("/vicon/vijeth_0/vijeth_0", TransformStamped, callbackVICON,0)
    	rospy.Subscriber("/vicon/vijeth_1/vijeth_1", TransformStamped, callbackVICON,1)
    	rospy.Subscriber("/vicon/vijeth_2/vijeth_2", TransformStamped, callbackVICON,2)
    	rospy.Subscriber("/vicon/vijeth_3/vijeth_3", TransformStamped, callbackVICON,3)

	#####################Change for each bot#########################
    	rospy.Subscriber("'botStatus0'", botStatus, callbackBotStatus,0)
    	rospy.Subscriber("'botStatus2'", botStatus, callbackBotStatus,2)
    	rospy.Subscriber("'botStatus3'", botStatus, callbackBotStatus,3)
    	#################################################################

    	#Time parameters and initialisation
	dt=0.01
	timeLoopPrev=rospy.get_time()

	timeForMotion=0.5 #We will allow the bot to move for half a second after it has computed the voronoi paritions.
	myStatus.isMoving=False
	rate = rospy.Rate(10)
    	while not rospy.is_shutdown():
    		#With the following step and with the callbacks we ensure that the bot knows the status of each ot in real time.  
    		botStatuses[BotNumber]=myStatus.isMoving

    		#Now we will check whether or not we can compute the Voronoi parition. It can be done if non of the bots are moving.
    		#Below statement means that all bots are stationary if 'any of the bots moving' is false.
    		allBotsStationary= not(botStatuses[0] or botStatuses[1] or botStatuses[2] or botStatuses[3])

      		if allBotsStationary:
    			#Computing the Voronoi partition for this bot.
    			partition=voronoi(pos_grid,N_bots,BotNumber,bot_loc)

   			#Computing the mean and weighted mean over this bot's partition.
   			mv=Mv(partition,K,a_hat,grid_Res)
   			lv=Lv(partition,pos_grid,K,a_hat,grid_Res)

   			#Computing the weighted centroid.
  			Cv=lv/mv
   			print "Centroid for bot 0", Cv
   			print "Locations for bot 0", bot_loc[:,0]
   			print "Locations for bot 1", bot_loc[:,1]
   			print "Locations for bot 2", bot_loc[:,2]
   			print "Locations for bot 3", bot_loc[:,3]

   			timeStartMotion=rospy.get_time()

   		#Once the Voronoi partitions are computed the bot will be given a green signal to start moving ahead.
   		#It will continue to move for the next timeForMotion duration.
   		if (rospy.get_time()-timeStartMotion)<timeForMotion:
   			#Setting the motion for this period.
   			pwmInput.rightInput=120
        		pwmInput.leftInput=120
        		pub_PWM.publish(pwmInput)

        	#Updating the isMoving status to true
	        	myStatus.isMoving=True
        		pub_myStatus.publish(myStatus)


        	#After timeForMotion all bots will stop because of the case below. Subsequently they will re-enter into Voronoi partition computation. 
   		elif (rospy.get_time()-timeStartMotion)>timeForMotion:
   			#Setting the motion for this period.
   			pwmInput.rightInput=0
        		pwmInput.leftInput=0
        		pub_PWM.publish(pwmInput)

       		 	#Updating the isMoving status to true
        		myStatus.isMoving=False
        		pub_myStatus.publish(myStatus)

       		#Setting the can

		#dt=rospy.get_time()-timeLoopEnd
		#q_dot = (q-q_prev)/dt

		#Velocity of states
		#vel = np.array((S(q).transpose()*S(q)).I*S(q).transpose()*np.matrix(q_dot))
		#print "vel:", vel
		#print "q_dot:", q_dot

		#Error in states
		#e=np.array(rot2body(q)*np.matrix(qr-q))
		#err_int=e*dt+err_int
		#print "e_int", err_int
		#Controller
		#u=np.array(vc_dot(e+ki*err_int,vel,vel_ref,w_ref)) + k4*(vc(e+ki*err_int,vel_ref,w_ref)-vel)

		#Torque to be sent at each instant
		#tau=np.array(torque(q,q_dot,vel,u))

		#Cponverting the torques to PWM inputs.
        	#pwmInput.rightInput=K_tau_R*tau[0][0]+K_wdot*wdotR+K_w*wR
        	#pwmInput.leftInput=K_tau_L*tau[1][0]+K_wdot*wdotR+K_w*wL
	    	#pub_PWM.publish(pwmInput)
		#q_prev=q
		#timeLoopEnd=rospy.get_time()

		rate.sleep()

if __name__ == '__main__':
    try:
        torqueController()
    except rospy.ROSInterruptException:
        pass
