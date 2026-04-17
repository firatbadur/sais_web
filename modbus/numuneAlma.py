import serial
import time

on = chr(2) + "O" + chr(1) + chr(1) + chr(3)
off = chr(2) + "O" + chr(1) + chr(0) + chr(3)

for i in range(0,19):
	print(i, ". tetikleme")

	ser = serial.Serial("COM5", 38400, timeout=2)
	# To close relay (ON)
	ser.write(bytes(on, encoding='utf-8'))
	print(bytes(on, encoding='utf-8'))
	ser.close()

	time.sleep(5)

	ser = serial.Serial("COM5", 38400, timeout=2)
	# To open relay (OFF)
	print(bytes(off, encoding='utf-8'))
	ser.write(bytes(off, encoding='utf-8'))
	ser.close()

	time.sleep(60)
