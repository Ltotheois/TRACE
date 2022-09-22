#!../venv/bin/python3
# -*- coding: utf-8 -*-

# Author: Luis Bonah

import time
import pyvisa
import numpy as np
import zhinst.ziPython as zi

SILENT = True

class CustomError(Exception):
	pass


class Synthesizer():
	def __init__(self, multiplication=None):
		if multiplication is None:
			raise CustomError("The multiplication factor was not specified.")
		self.multiplication = multiplication

	def set_frequency(self, value):
		if not SILENT:
			print(f"SETTING FREQUENCY TO {value}")
	
	def set_rfpower(self, value, blocking=True):
		if not SILENT:
			print(f"SETTING RF FREQUENCY TO {value}")

class LockInAmplifier():
	def __init__(self):
		self.readmag = False
	
	def get_intensity(self):
		if not SILENT:
			print("GETTING INTENSITY")
		return(float(round(time.time()))/10000)
	
	def measure_intensity(self):
		counterstart = time.perf_counter()
		while time.perf_counter() - counterstart < self.dttc:
			continue

		return(self.get_intensity())


class MockDevice(Synthesizer, LockInAmplifier):
	EOL = ";"
	def __init__(self, address, multiplication=1):
		Synthesizer.__init__(self, multiplication=multiplication)
		LockInAmplifier.__init__(self)
	
	def check_errors(self):
		if not SILENT:
			print("CHECKING ERRORS")
	
	def prepare_measurement(self, dict_, devicetype):
		if "lockin_timeconstant" in dict_ and "lockin_delaytime" in dict_:
			tc = float(dict_["lockin_timeconstant"].replace("μs", "E-3").replace("ms", "").replace("ks", "E6").replace("s", "E3"))
			self.dttc = (dict_["lockin_delaytime"] + tc)/1000
		if not SILENT:
			print("SETTING START VALUES")

	def close(self):
		pass


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

class SCPIDevice():
	EOL = "\n"
	
	def __init__(self, visa_address):
		rm = pyvisa.ResourceManager()
		self.connection = None
		self.connection = rm.open_resource(visa_address)
		
	def check_errors(self):
		response = self.connection.query(f"*CLS {self.EOL}SYST:SERR?")
		if int(response[0]) != 0:
			raise CustomError(f"Device has static errors:\n{response}")
	
	def prepare_measurement(self, dict_, devicetype):
		pass

	def set_values(self, dict_, blocking=True):
		for key, value in dict_.items():
			self.connection.write(f"{key} {value}")
		
		if blocking:
			self.connection.query("*OPC?")

	def close(self):
		if self.connection:
			self.connection.close()


class SCPISynthesizer(Synthesizer, SCPIDevice):
	def __init__(self, address, multiplication=None):
		Synthesizer.__init__(self, multiplication=multiplication)
		SCPIDevice.__init__(self, address)
	
	def set_frequency(self, value):
		factor = self.multiplication
		response = self.connection.query(f"FREQ:CW {value/factor}MHZ {self.EOL}*OPC?")
		
		if int(response) != 1:
			raise CustomError(f"Could not set frequency of {value}MHz.")
	
	def set_rfpower(self, value, blocking=True):
		if blocking:
			self.connection.query(f":OUTP:STATe {value} {self.EOL}*OPC?")
		else:
			self.connection.write(f":OUTP:STATe {value}")
	
	def prepare_measurement(self, dict_, devicetype):
		# Create repeatable default state
		self.connection.write("*RST")
		self.check_errors()
		
		# Set initial frequency to avoid damage
		init_frequency = dict_[f"{devicetype}_frequency"].frequencies[0]
		self.set_frequency(init_frequency)
		
		# Find startvalues
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
					"SOUR:FM1:STAT":	"OFF",
					"SOUR:LFO":			"OFF",
			})
			
			if dict_["general_mode"] == "tandem":
				startvalues.update({
					"SOUR:FM1:SOUR":	"EXT1",
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
			
			elif dict_["general_mode"] == "tandem":
				startvalues.update({
					# Turn on Pulse Modulation
					"SOUR:PULM:STAT":				"ON",
					"SOUR:PULM:SOUR":				"EXT",
					"SOUR:PULM:TRIG:EXT:LEV":		"TTL",
				})
		
		# Set start values
		self.set_values(startvalues)
		
		# Turn on RF power
		self.connection.query(f":OUTP:STATe 1 {self.EOL}*OPC?")
	
	def close(self):
		# Turn off RF power
		self.connection.write(":OUTP:STATe 0")
		
		# Go to local -> unlock front panel controls
		self.connection.write("&GTL")
		
		super().close()

class Agilent8257d(SCPISynthesizer):
	pass

class RSSMF100A(SCPISynthesizer):
	pass


class SignalRecovery7265(LockInAmplifier, SCPIDevice):
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
	
	def __init__(self, address):
		LockInAmplifier.__init__(self)
		SCPIDevice.__init__(self, address)
	
	def measure_intensity(self):
		counterstart = time.perf_counter()
		while time.perf_counter() - counterstart < self.dttc:
			continue

		return(self.get_intensity())
	
	def measure_intensity_dmdr_digital(self):
		results = {}
		for state in (0, 1):
			self.pump.set_rfpower(state)
			counterstart = time.perf_counter()
			while time.perf_counter() - counterstart < self.dttc:
				continue
			results[state] = self.get_intensity()
		
		return(results[1] - results[0])
	
	def get_intensity(self):
		if self.readmag:
			return(float(self.connection.query("MAG.?")))
		else:
			return(float(self.connection.query("X.?")))
		
	def prepare_measurement(self, dict_, devicetype):
		# Create repeatable default state
		# Problem is phase, which might get lost!
		# self.connection.write("ADF 1")
		self.check_errors()

		# Set special options
		if dict_.get("static_lockinreadmag"):
			self.readmag = True
		
		if dict_.get("general_mode") == "digital_dmdr":
			self.measure_intensity = self.measure_intensity_dmdr_digital
		
		# Create format that is understood by lockin amplifier
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

		# Find startvalues
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

		# Set start values
		self.set_values(startvalues)

	def check_errors(self):
		pass

class ZurichInstrumentsMFLI(LockInAmplifier):
	def __init__(self, visa_address):
		LockInAmplifier.__init__(self)
		self.daq = zi.ziDAQServer(visa_address, 8004)

		# @Luis Just for testing
		self.rate = 15E5
		self.duration = 0.2
		ts, ys = self.get_signal(self.rate, self.duration)
		self.data = np.zeros((1000, len(ts)))
		self.data[0] = ts
		self.i = 1

	def check_errors(self):
		pass
	
	def prepare_measurement(self, dict_, devicetype):
		tc = float(dict_["lockin_timeconstant"].replace("μs", "E-6").replace("ms", "E-3").replace("ks", "E3").replace("s", ""))
		self.dttc = dict_["lockin_delaytime"]/1000 + tc
		
		if dict_.get("static_lockinreadmag"):
			self.readmag = True
		
		# External 10 MHz reference
		self.daq.setInt('/dev4055/system/extclk', 1)
		
		if dict_["general_mode"] == "classic":
			self.daq.setInt('/dev4055/demods/0/enable', 1)
			self.daq.setInt('/dev4055/demods/1/enable', 1)
		
			self.daq.setInt('/dev4055/sigouts/0/on', 0)
			self.daq.setInt('/dev4055/sigouts/0/enables/0', 0)
			self.daq.setInt('/dev4055/sigouts/0/enables/1', 0)
			self.daq.setInt('/dev4055/sigouts/0/enables/2', 0)
			self.daq.setInt('/dev4055/sigouts/0/enables/3', 0)
			
			self.daq.setInt('/dev4055/demods/0/oscselect', 0)
			self.daq.setDouble('/dev4055/demods/0/harmonic', 2)
			self.daq.setInt('/dev4055/demods/0/order', 1) # Order of low-pass filter
			
			self.daq.setInt('/dev4055/demods/1/oscselect', 0)
			self.daq.setInt('/dev4055/demods/1/adcselect', 8) # Aux1 as input for demod1
			self.daq.setInt('/dev4055/extrefs/0/enable', 1)

			self.daq.setDouble('/dev4055/demods/0/timeconstant', tc)

	def get_signal(self, samplefrequency, duration, records=1, timeout=2):
		sco = self.daq.scopeModule()
		sco.execute()
		
		sti = int(-np.log2(samplefrequency / 60E6))
		samplefrequency = 60E6 / 2**sti
		if sti < 0:
			raise ValueError("Sampling rate cannot be higher than 60 MHz")
		elif sti > 16:
			raise ValueError("Sampling rate cannot be smaller than 916 MHz")
		
		points = int(samplefrequency * duration)
		if points < 4096:
			raise ValueError("Recorded points cannot be less than 4096.")
		elif points > 5.12E6:
			raise ValueError("Recorded points cannot be more than 5120000")
		
		# Set sample frequency
		self.daq.setInt('/dev4055/scopes/0/time', sti)
		
		# Set sample points
		self.daq.setInt('/dev4055/scopes/0/length', points)

		# Set input for channel 0 (0 == signal input 1)
		self.daq.setInt('/dev4055/scopes/0/channels/0/inputselect', 0)
	
		# Set active channels (1 == only channel 1)
		self.daq.setInt('/dev4055/scopes/0/channel', 1)
	
	
		# Set Time Domain mode
		sco.set('scopeModule/mode', 1)

		# Set averager to None
		sco.set('scopeModule/averager/weight', 1)


		sco.subscribe("/dev4055/scopes/0/wave")
		self.daq.setInt("/dev4055/scopes/0/enable", 1)
		self.daq.sync()


		st = time.perf_counter()
		progress = 0
		num_records=1

		while (records < num_records) or (progress < 1.0):
			time.sleep(0.1)
			records = sco.getInt("records")
			progress = sco.progress()[0]
			if (time.perf_counter() - st) > timeout:
					raise ValueError(f"Recording the desired time signal took longer than the timeout of {timeout} s.")
					break

		self.daq.setInt("/dev4055/scopes/0/enable", 0)
		data = sco.read(True)
		sco.finish()

		record = data["/dev4055/scopes/0/wave"][0]
		dt = record[0]["dt"]
		totalsamples = record[0]["totalsamples"]

		ts = np.arange(0, totalsamples) * dt
		ys = record[0]["wave"][0]
		
		result = [ts] + [record[0]["wave"][0] for record in data["/dev4055/scopes/0/wave"]]
		return(result)
	
	def get_intensity(self):
		sample = self.daq.getSample("/dev4055/demods/0/sample")
		if self.readmag:
			x, y = sample["x"][0], sample["y"][0]
			return((x**2 + y**2)**0.5)
		else:
			return(sample["x"][0])
	
	def measure_intensity(self):
		counterstart = time.perf_counter()
		while time.perf_counter() - counterstart < self.dttc:
			continue

		return(self.get_intensity())
	
	# @Luis Just for testing
	def measure_intensity(self):
		counterstart = time.perf_counter()
		while time.perf_counter() - counterstart < 0.01:
			continue
		
		ts, ys = self.get_signal(self.rate, self.duration)
		self.data[self.i] = ys
		self.i += 1
		return(self.i)
	
	def close(self):
		# @Luis Just for testing
		np.save(r"C:\Users\midascoins\Desktop\timesignal.npy", self.data[:self.i])
		self.daq.disconnect()


def connect(mdict, devicetype):
	devicename = mdict[f"static_{devicetype}device"]
	deviceaddress = mdict[f"static_{devicetype}address"]
	deviceclass = deviceclasses[devicetype][devicename]
	
	if devicetype == "lockin":
		device = deviceclass(deviceaddress)
	elif devicetype in ["probe", "pump"]:
		multiplication = mdict[f"static_{devicetype}multiplication"]
		device = deviceclass(deviceaddress, multiplication=multiplication)
	
	device.prepare_measurement(mdict, devicetype)
	return(device)

deviceclasses = {
	"probe": {cls.__name__: cls for cls in Synthesizer.__subclasses__()},
	"pump": {cls.__name__: cls for cls in Synthesizer.__subclasses__()},
	"lockin": {cls.__name__: cls for cls in LockInAmplifier.__subclasses__()},
}

modes = ("classic", "dr", "dmdr", "dmdr_am", "dr_pufm", "tandem", "digital_dmdr")

if __name__ == "__main__":
		pass
		# lock_in = SignalRecovery7265("GPIB::12")
		# probe = agilent8257d(3,"TCPIP::192.168.23.48::INST0::INSTR")
		# pump = rssmf100a(6,"TCPIP::192.168.23.51::INST0::INSTR")
