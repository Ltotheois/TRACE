#!/usr/bin/env python
# -*- coding: utf-8 -*-

# Author: Luis Bonah
# Description: Operate electronic valves of spectrometer, initial values of valves are closed

import os, sys, time
import serial
import argparse
import numpy as np
import traceback as tb

## Options for Initializing Serial Devices
kwargs_serial_gauge = {
	"baudrate":				2400,
	"timeout":				1,
}
kwargs_serial_valves = {
	"timeout":				1,
}

## Translate units into mbar
pressure_translation = {
	"mbar":						1,
	"torr":						1.33322,
	"pa":						0.01,
	"micron":					0.001,
}

## Default Values
c =	{
	"pressure_min"				: 6e-6,
	"pressure_max"				: 8e-6,
	"pressure_threshold"		: 8e-6,
	"pressure_empty"			: 4.5e-6,
	"pressure_gauge_port"		: "COM2",
	"valve_inlet_port"			: "COM4",
	"valve_outlet_port"			: "COM5",
	"measurements_mean_number"	: 10,
	"diff_outlier"				: 0.15,
	"diff_faulty_value"			: 0.50,
	"diff_below_threshold"		: 0.05,
	"pulse_duration"			: 0.1,
	"timeout_pressure_low"		: 600,
	"timeout_below_threshold"	: 600,
	"timeout_after_filling"		: 10,
	"timeout_evacuating"		: 5,
	"timeout_evacuating_ut"		: 5,
	"timeout_final"				: 20,
	"timeout_switch_state"		: 1,
	"force"						: False,
	"DEBUG"						: False,
	"DUMMY"						: False,
}

class CustomError(Exception):
	pass


## Dummy Classes for Testing
class DummyDevice():
	def __init__(self, name):
		self.name = name

	def close(self):
		pass
		
	def write(self, command):
		pass

class DummyValve(DummyDevice):
	state = True
	in_waiting = 100
	def read(self, length):
		return([255] if self.state else [0])
		
	def write(self, command):
		if command == b"\x00":
			return
			self.state = not self.state

class DummyPressureGauge(DummyDevice):
	def readline(self):
		channel = "DUMMY"
		pressure = float(input("Enter Pressure at pressure gauge [ubar]: "))/1000
		return(f"{channel}:mbar:{pressure}".encode("utf-8"))



def output(message):
	print(message)

def debug(message):
	if c["DEBUG"]:
		output(message)

def detect_outlier(values):
	values = np.array(values)
	diff = (values-np.mean(values))/values
	clean_values = values[diff < c["diff_outlier"]]
	clean = len(clean_values) == len(values)
	mean = np.mean(clean_values)
	
	return(clean, mean)
	
def switch_valve(device):
	command = b'\x00'
	device.write(command)
	
def open_valve(device):
	debug(f"Opening valve {device.name}")
	if get_state(device) == False:
		switch_valve(device)
		time.sleep(c["timeout_switch_state"])
		if get_state(device) == False:
			raise CustomError(f"Could not open valve {device.name}.")

def close_valve(device):
	debug(f"Closing valve {device.name}")
	if get_state(device) == True:
		switch_valve(device)
		time.sleep(c["timeout_switch_state"])
		if get_state(device) == True:
			raise CustomError(f"Could not close valve {device.name}.")

# True corresponds to open corresponds to 255
# False corresponds to closed
def get_state(device):
	st = time.perf_counter()
	while not device.in_waiting and time.perf_counter() - st < 1:
		pass
	response = device.read(device.in_waiting)
	state = (response[-1] == 255)
	debug(f"State of device {device.name} is {state}")
	return(state)

def measure_pressure(device):
	command = "MES R TM2\r\n"
	command = command.encode("utf-8")
	device.write(command)
	response = device.readline().decode("utf-8")

	if not response:
		raise CustomError("Could not read pressure. Response was empty.")
	else:
		channel, unit, value = response.split(":")
		unit_factor = pressure_translation[unit.lower().strip()]
		pressure = np.float64(value)*unit_factor
		return(pressure/1000)

def measure_pressure_mean(device):
	pressures = [measure_pressure(device) for x in range(c["measurements_mean_number"])]
	clean, mean = detect_outlier(pressures)
	return(mean)

def measure_pressure_wrapper(address):
	try:
		pressure_gauge_device = serial.Serial(port=address, **kwargs_serial_gauge)
		pressure = measure_pressure(pressure_gauge_device)
		return(pressure)
	except Exception as E:
		return(E)

def set_pressure(c, gauge, inlet, outlet):
	# Gauge does not respond on first query
	try:
		measure_pressure(gauge)
	except:
		pass
	
	# Initialize valves
	close_valve(inlet)
	close_valve(outlet)

	curr_pressure = measure_pressure_mean(gauge)
	output(f"START: The current pressure is {curr_pressure:.2E} bar.")
	if c['pressure_min'] < curr_pressure < c['pressure_max'] and not c["force"]:
		output(f"Pressure is already in desired range of {c['pressure_min']:.2E} to {c['pressure_max']:.2E} bar.")
		return(1)
	elif c['pressure_min'] < curr_pressure < c['pressure_threshold'] and not c["force"]:
		output(f"Pressure is already in accepted range of {c['pressure_min']:.2E} to {c['pressure_threshold']:.2E} bar.")
		return(1)
	else:
		## Evacuating Cell
		output("EVACUATING: Opening outlet.")
		open_valve(outlet)
		
		starttime = time.time()
		
		pressures = []
		curr_pressure = measure_pressure(gauge)
		pressures.append(curr_pressure)
		diff = 0
		while curr_pressure > c["pressure_empty"] or diff > c["diff_faulty_value"]:
			time.sleep(c["timeout_evacuating"])
			curr_pressure = measure_pressure(gauge)
			pressures.append(curr_pressure)
			
			diff = abs(pressures[-1]-pressures[-2])/pressures[-1]
			pressures.pop(0)
			
			if time.time()-starttime > c["timeout_pressure_low"]:
				raise CustomError(f"Evacuating cell took longer than the specified {c['timeout_pressure_low']:.2f} seconds.")
		
		## Pressure under Minimum Threshold, checking if it decreases any more
		output("EVACUATING UNDER THRESHOLD: Pressure is under threshold, checking if it decreases any more.")
		below_thresholdtime = time.time()
		
		pressures = []
		curr_pressure = measure_pressure(gauge)
		pressures.append(curr_pressure)
		diff = 1
		while diff > c["diff_below_threshold"]:
			time.sleep(c["timeout_evacuating_ut"])
			curr_pressure = measure_pressure(gauge)
			pressures.append(curr_pressure)
			
			diff = abs(pressures[-1]-pressures[-2])/pressures[-1]
			pressures.pop(0)
			
			if time.time()-below_thresholdtime > c["timeout_below_threshold"]:
				output(f"Could not reach a stable pressure beneath the threshold as the time threshold was reached ({c['timeout_below_threshold']} s).")
		close_valve(outlet)
		
		## Pressure at minimum, filling cell
		output("FILLING: Pressure is at its minimum, filling the cell.")
		# Use switch valve here to be faster
		
		while measure_pressure(gauge) < c['pressure_min']:
			switch_valve(inlet)
			st = time.perf_counter()
			while time.perf_counter() - st < c['pulse_duration']:
				pass
			switch_valve(inlet)
		
		## Evacuate to desired pressure range
		output("EVACUATING TO RANGE: Evacuating to desired range.")
		time.sleep(c["timeout_after_filling"])
		
		if measure_pressure_mean(gauge) > c['pressure_max']:
			open_valve(outlet)
			while measure_pressure(gauge) > (c['pressure_min'] + c['pressure_max'])/2:
				pass
			close_valve(outlet)
		
		## Done with filling, wait before measuring final pressure, to let pressure settle
		output("SETTLING: Waiting before measuring final pressure.")
		time.sleep(c["timeout_final"])
		curr_pressure = measure_pressure_mean(gauge)
		
		## Check if filling was successful
		if c['pressure_min'] < curr_pressure < c['pressure_max']:
			output(f"Filling of cell was successful, with the current pressure being {curr_pressure:.2E} bar, which is in the limit of {c['pressure_min']:.2E} bar to {c['pressure_max']:.2E} bar.")
			return(1)
		
		else:
			output(f"Filling the cell did not succeed! The current pressure of {curr_pressure:.2E} bar missed the specified range of {c['pressure_min']:.2E}-{c['pressure_max']:.2E} bar.")
			return(0)


def main(config):
	c.update(config)
	active_devices = []
	try:
		if c["DUMMY"] == False:
			pressure_gauge_device		= serial.Serial(port=c["pressure_gauge_port"], **kwargs_serial_gauge)
			active_devices.append(pressure_gauge_device)
			valve_inlet_device			= serial.Serial(port=c["valve_inlet_port"], **kwargs_serial_valves)
			active_devices.append(valve_inlet_device)
			valve_outlet_device			= serial.Serial(port=c["valve_outlet_port"], **kwargs_serial_valves)
			active_devices.append(valve_outlet_device)
		else:
			pressure_gauge_device		= DummyPressureGauge("Pressure-Gauge")
			valve_inlet_device			= DummyValve("Inlet-Valve")
			valve_outlet_device			= DummyValve("Outlet-Valve")

		rc = set_pressure(c, pressure_gauge_device, valve_inlet_device, valve_outlet_device)
		rm = ""
	except CustomError as E:
		rc = -1
		rm = str(E)
		output(rm)
	except Exception as E:
		rc = -1
		rm = f"Unexpected error occurred. Error message reads:\n{str(E)}\n\n{tb.format_exc()}\n\n"
		output(rm)
	finally:
		for device in active_devices:
			device.close()
		
		return(rc, rm)
	
if __name__ == "__main__":
	# Command Line Parsing
	parser = argparse.ArgumentParser()
	for key, value in c.items():
		if type(value) == bool:
			parser.add_argument(f"--{key}", action="store_true", default=value)
		else:
			parser.add_argument(f"--{key}", type=type(value), default=value)
	values = vars(parser.parse_args())

	main(values)