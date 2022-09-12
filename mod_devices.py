#!../venv/bin/python3
# -*- coding: utf-8 -*-

# Author: Luis Bonah

import time
import pyvisa


class SCPIAttribute():
	def __init__(self, options=None, range_=None, readonly=False, novalue=False):
		self.readonly = readonly
		self.novalue = novalue
		self.options = options
		self.range_ = range_
	
	def validate(self, value):
		if self.readonly:
			raise ValueError("Trying to set value on readonly SCPI attribute.")
		
		if self.novalue:
			if value:
				raise ValueError("Trying to provide a value for a command with no argument.")
		
		if self.options:
			if value not in self.options:
				raise ValueError(f"The value '{value}' is no valid option for this SCPI attribute.")
		
		elif self.range_:
			min_, max_ = self.range_
			if min_ > value:
				raise ValueError(f"The value '{value}' is smaller than the allowed minimum '{min_}'.")
			if max_ < value:
				raise ValueError(f"The value '{value}' is larger than the allowed maximum '{max_}'.")

class CustomError(Exception):
	pass

class device():
	EOL = "\n"
	
	def __init__(self, visa_address):
		rm = pyvisa.ResourceManager()
		self.connection = None
		self.connection = rm.open_resource(visa_address)
	
	def clear_and_check(self):
		# @Luis:
		# *RST resets the instrument to its preset state -> first check that this does not cause any errors
		# response = self.connection.query(f"*RST {self.EOL}*CLS {self.EOL}SYST:SERR?")
		response = self.connection.query(f"*CLS {self.EOL}SYST:SERR?")
		if int(response[0]) != 0:
			raise CustomError(f"Device has static errors:\n{response}")
		
	def prepare_for_measurement(self, dict_, devicetype):
		self.clear_and_check()
	
	def query_errors(self):
		error_list = []
		response = self.connection.query("SYST:ERR?")
		
		while int(response[0]) != 0:
			error_list.append(tmp_resp)
			response = self.connection.query("SYST:ERR?")
		static_error_list = self.connection.query("SYST:SERR?")
		return(error_list, static_error_list)
	
	def close(self):
		if self.connection:
			self.connection.close()

class mock_device(device):
	EOL = ";"
	
	def __init__(self, *args, **kwargs):
		pass
		
	class connection():
		def write(self,arg):
			print(f"Writing to mock device: {arg}")
		def query(self,arg):
			print(f"Query the mock device: {arg}")
		def close():
			print("Closed mock device")
			
	def prepare_for_measurement(self, dict_, devicetype):
		if "lockin_timeconstant" in dict_ and "lockin_delaytime" in dict_:
			tc = float(dict_["lockin_timeconstant"].replace("μs", "E-3").replace("ms", "").replace("ks", "E6").replace("s", "E3"))
			self.dttc = (dict_["lockin_delaytime"] + tc)/1000
		print("Setting start values")
	
	def query_errors(self):
		print("Querying errors")
	
	def measure_intensity(self):
		counterstart = time.perf_counter()
		while time.perf_counter() - counterstart < self.dttc:
			continue

		return(self.get_intensity())
		
	def get_intensity(self):
		print("Getting X")
		import time
		return(float(round(time.time()))/10000)
		
	def set_frequency(self, freq):
		print(f"setting frequency to {freq}")

class lock_in_7265(device):
	EOL = ";"
	
	SEN_OPTIONS = {18: '1mV', 19: '2mV', 20: '5mV', 21: '10mV', 22: '20mV', 23: '50mV', 24: '100mV', 25: '200mV', 26: '500mV', 27: '1V'}
	IE_OPTIONS = {0: "Internal", 1: "External Logic (Rear)", 2: "Extern (Front)"}
	SLOPE_OPTIONS = {0:"6 dB/octave",1:"12 dB/octave",2:"18 dB/octave",3:"24 dB/octave"}
	TC_OPTIONS = {0: '10μs', 1: '20μs', 2: '40μs', 3: '80μs', 4: '160μs', 5: '320μs', 6: '640μs', 7: '5ms', 8: '10ms', 9: '20ms', 10: '50ms', 11: '100ms', 12: '200ms', 13: '500ms', 14: '1s', 15: '2s', 16: '5s', 17: '10s', 18: '20s', 19: '50s', 20: '100s', 21: '200s', 22: '500s', 23: '1ks', 24: '2ks', 25: '5ks', 26: '10ks', 27: '20ks', 28: '50ks', 29: '100ks'}
	REMOTE_OPTIONS = {0: "Local", 1: "Remote"}
	ADF_OPTIONS = {0:"Factory Default", 1:"Factory Default except connection"}
	VMODE_OPTIONS = {0:"Both inputs grounded (test mode)", 1:"A input only", 2:"-B input only", 3:"A-B differential mode"}
	ACGAIN_OPTIONS = {0: '0dB', 1: '10dB', 2: '20dB', 3: '30dB', 4: '40dB', 5: '50dB', 6: '60dB', 7: '70dB', 8: '80dB', 9: '90dB'}
	
	ATTRIBUTES= {
		"SEN": 			SCPIAttribute(options=SEN_OPTIONS),
		"IE": 			SCPIAttribute(options=IE_OPTIONS), 
		"AQN": 			SCPIAttribute(novalue=True), #autophase
		"SLOPE": 		SCPIAttribute(options=SLOPE_OPTIONS),
		"TC": 			SCPIAttribute(options=TC_OPTIONS),
		"X": 			SCPIAttribute(readonly=True),
		"Y": 			SCPIAttribute(readonly=True),
		"MAG?": 		SCPIAttribute(readonly=True),
		"PHA?": 		SCPIAttribute(readonly=True),
		"MP?": 			SCPIAttribute(readonly=True),
		"NN?": 			SCPIAttribute(readonly=True),
		"OF": 			SCPIAttribute(range_=[0, 2.5E8]), #in mHz, 0-250kHz
		"REMOTE":		SCPIAttribute(options=REMOTE_OPTIONS),
		"ADF": 			SCPIAttribute(options=ADF_OPTIONS),
		"VMODE": 		SCPIAttribute(options=VMODE_OPTIONS),
		"ACGAIN":		SCPIAttribute(options=ACGAIN_OPTIONS),
		"OA": 			SCPIAttribute(range_=[0, 5000000]), #0 to  according to 0 to 5V
		"REFN": 		SCPIAttribute(range_=[1, 65535]),
	}
	
	def measure_intensity(self):
		counterstart = time.perf_counter()
		while time.perf_counter() - counterstart < self.dttc:
			continue

		return(self.get_intensity())
		
	def get_intensity(self):
		return(float(self.connection.query("X.?")))
		
	def prepare_for_measurement(self, dict_, devicetype):
		super().prepare_for_measurement(dict_, devicetype)

		tc = float(dict_["lockin_timeconstant"].replace("μs", "E-3").replace("ms", "").replace("ks", "E6").replace("s", "E3"))
		self.dttc = (dict_["lockin_delaytime"] + tc)/1000
		
		for key, options in (("lockin_timeconstant", self.TC_OPTIONS), ("lockin_acgain", self.ACGAIN_OPTIONS), ("lockin_sensitivity", self.SEN_OPTIONS)):
			value = dict_[key]
			
			if isinstance(value, int):
				pass
			elif isinstance(value, str) and value in options.values():
				keys, values = list(options.keys()), list(options.values())
				dict_[key] = keys[values.index(value)]
			else:
				raise CustomError(f"Could not convert value '{value}' for key '{key}'.")

		startvalues = {
			"IE": 		2,
			"REFN": 	2,
			"VMODE": 	1,
			"SLOPE": 	0,
			"TC": 		dict_["lockin_timeconstant"],
			"SEN": 		dict_["lockin_sensitivity"],
			"ACGAIN": 	dict_["lockin_acgain"],
		}
		
		if dict_["general_mode"] == "dmdr":
			startvalues.update({
				"REFN":	1,
			})

		for key, value in startvalues.items():
			self.connection.write(f"{key} {value}")

	def clear_and_check(self):
		# @Luis: Check if there is any error query for this device (SYST:SERR? is not understood)
		# and if there is anything that should be done before the measurement (cls, reset, ...)
		pass

class Synthesizer(device):
	
	def __init__(self, *args, multiplication=None, **kwargs):
		super().__init__(*args, **kwargs)
		if multiplication is None:
			raise CustomError("The multiplication factor was not specified.")
		self.multiplication = multiplication
	
	def set_frequency(self, frequency):
		frequency = frequency / self.multiplication
		response = self.connection.query(f"FREQ:CW {frequency}MHZ {self.EOL}*OPC?")
		
		if int(response) != 1:
			raise CustomError(f"Could not set frequency of {frequency}MHz.")
	
	def prepare_for_measurement(self, dict_, devicetype):
		super().prepare_for_measurement(dict_, devicetype)
		
		# Delete the following line if super class inlcudes the reset
		self.connection.write("*RST")
		
		initial_frequeny = self[f"{devicetype}_frequency"].frequencies[0]
		self.set_frequency(initial_frequency)
		
		startvalues = {
			# Go to remote
			"&GTR": 				"",
		
			# Set RF output power
			"SOUR:POW": 			dict_[f"{devicetype}_power"],
		
			# Set external reference
			"SOUR:ROSC:SOUR":		"EXT",
			"SOUR:ROSC:OUTP:SOUR":	"EXT",
		}
		
		
		if devicetype == "probe":
			startvalues.update({
				# Set FM modulation
				"SOUR:FM1:STAT": 		"ON",
				"SOUR:FM1:DEV": 		str(dict_["lockin_fmamplitude"] / self.multiplication) + "kHz",
				
				# Set FM modulation signal
				"SOUR:LFO":				"ON",
				"SOUR:LFO1:FREQ":		str(dict_["lockin_fmfrequency"]) + "Hz",
			})
			
			if dict_["general_mode"] == "dr_pufm":
				startvalues.update({
					"SOUR:FM1:STAT": 	"OFF",
					"SOUR:LFO":			"OFF",
			})
		
		elif devicetype == "pump":
			
			if dict_["general_mode"] == "dmdr":
				startvalues.update({
					# Turn on LFO and set FM1 values
					"SOUR:LFO": 					"ON",
					"SOUR:FM1:STAT": 				"ON",
					"SOUR:FM1:DEV": 				dict_["general_dmjump"]/dict_["static_pumpmultiplication"]/2,
					
					"SOUR:LFO1:SHAP": 				"SQU",
					"SOUR:LFO1:SHAP:PULS:PER":		dict_["general_dmperiod"],
					"SOUR:LFO1:SHAP:PULS:DCYC": 	50,
				})
			elif dict_["general_mode"] == "dmdr_am":
				startvalues.update({
					# Turn on LFO and set AM1 values
					"SOUR:LFO": 					"ON",
					"SOUR:AM1:STAT": 				"ON",
					"SOUR:AM1:DEPT": 				100,
					
					"SOUR:LFO1:SHAP": 				"SQU",
					"SOUR:LFO1:SHAP:PULS:PER":		str(dict_["general_dmperiod"]) + "ms",
					"SOUR:LFO1:SHAP:PULS:DCYC": 	50,
				})
			elif dict_["general_mode"] == "dr_pufm":
				startvalues.update({
					# Turn on LFO and set FM1 values
					"SOUR:LFO": 					"ON",
					"SOUR:FM1:STAT": 				"ON",
					"SOUR:FM1:DEV": 				str(dict_["lockin_fmamplitude"] / self.multiplication) + "kHz",
					"SOUR:LFO1:FREQ":				str(dict_["lockin_fmfrequency"]) + "Hz",
				})
					
		
		for key, value in startvalues.items():
			self.connection.write(f"{key} {value}")
		
		self.connection.query("*OPC?")
		
		# Turn on RF power
		self.connection.write(":OUTP:STATe 1")

	
	def close(self):
		self.connection.write(":OUTP:STATe 0")
		self.connection.write("&GTL")
		super().close()

class agilent8257d(Synthesizer):
	pass

class rssmf100a(Synthesizer):
	pass

def connect(self, devicetype):
	deviceclass = deviceclasses[devicetype][self[f"static_{devicetype}device"].lower()]
	deviceaddress = self[f"static_{devicetype}address"]
	
	if devicetype == "lockin":
		device = deviceclass(deviceaddress)
	elif devicetype in ["probe", "pump"]:
		multiplication = self[f"static_{devicetype}multiplication"]
		device = deviceclass(deviceaddress, multiplication=multiplication)
	device.prepare_for_measurement(self, devicetype)
	return(device)

deviceclasses = {
	"probe": {
		"mock device" :		mock_device,
		"agilent 8257d" :	agilent8257d,
		"rs smf 100a" :		rssmf100a,
	},
	"pump": {
		"mock device" :		mock_device,
		"rs smf 100a" :		rssmf100a,
	},
	"lockin": {
		"mock device" :		mock_device,
		"7265 lock in" :	lock_in_7265,
	},
}

if __name__ == "__main__":
		pass
		# lock_in = lock_in_7265("GPIB::12")
		# probe = agilent8257d(3,"TCPIP::192.168.23.48::INST0::INSTR")
		# pump = rssmf100a(6,"TCPIP::192.168.23.51::INST0::INSTR")
