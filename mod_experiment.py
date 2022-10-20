#!../venv/bin/python3
# -*- coding: utf-8 -*-

# Author: Luis Bonah

import os
import sys
import time
import json
import traceback
import pandas as pd
import numpy as np
import threading
import websockets
import asyncio
from multiprocessing import shared_memory
from collections import deque
from datetime import datetime
import configparser

homefolder = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, homefolder)

import mod_devices as devices
import mod_pumpcell

URL, PORT = "localhost", 8112

class Tee(object):
	def __init__(self, name, mode, stderr=False):
		self.file = open(name, mode)
		self.std = sys.stderr if stderr else sys.stdout
		if stderr:
			sys.stderr = self
		else:
			sys.stdout = self
	def __del__(self):
		if stderr:
			sys.stderr = self.std
		else:
			sys.stdout = self.std
		self.file.close()
	def write(self, data):
		self.file.write(data)
		self.std.write(data)
	def flush(self):
		self.file.flush()

class CustomError(Exception):
	pass

class CustomValueError(Exception):
	pass

class UserAbort(Exception):
	pass

class pint(int):
	def __new__(cls, *args, **kwargs):
		tmp = super().__new__(cls, *args, **kwargs)
		if tmp > 0:
			return(tmp)
		else:
			raise ValueError("The value of a pint has to be positive, the provided value was {tmp}.")

class pfloat(float):
	def __new__(cls, *args, **kwargs):
		tmp = super().__new__(cls, *args, **kwargs)
		if tmp >= 0:
			return(tmp)
		else:
			raise ValueError(f"The value of a pfloat has to be positive, the provided value was {tmp}.")

class Sweep(dict):
	_draft = {
		"mode": {"fixed", "sweep"},
		"direction": {"forth", "forthback", "back", "backforth", "fromcenter", "random"},
	}

	def __init__(self, dict_={}, **kwargs):
		dict_.update(**kwargs)
	
		if "iterations" not in dict_:
			dict_["iterations"] = 1
		else:
			try:
				dict_["iterations"] = int(dict_["iterations"])
			except ValueError as E:
				raise ValueError(f"The value for the iterations '{dict_['iterations']}' could not be converted to an integer.")
		
		key = "mode"
		if not key in dict_:
			raise ValueError(f"The Sweep object is missing the '{key}' parameter.")
			if dict_[key] not in self._draft[key]:
				raise ValueError(f"The argument '{dict_[key]}' for the '{key}' parameter is not understood. Please use one of the following values: {_draft[key]}")
			
		if dict_["mode"] != "fixed":
			key = "direction"
			if not key in dict_:
				raise ValueError(f"The Sweep object is missing the '{key}' parameter.")
				if dict_[key] not in self._draft[key]:
					raise ValueError(f"The argument '{dict_[key]}' for the '{key}' parameter is not understood. Please use one of the following values: {_draft[key]}")

		if dict_["mode"] == "fixed":
			try:
				center = pfloat(dict_["center"])
			except ValueError as E:
				raise ValueError(f"The center value has to be a positive numeric value. The value {dict_['center']} could not be converted to a positive numeric value.")
				
			frequencies = np.array((center,))
			
		else:
			
			if "center" in dict_ and "span" in dict_:
				try:
					center = pfloat(dict_["center"])
				except ValueError as E:
					raise ValueError(f"The center value has to be a positive numeric value. The value {dict_['center']} could not be converted to a positive numeric value.")
				
				try:
					span = pfloat(dict_["span"])
				except ValueError as E:
					raise ValueError(f"The span value has to be a positive numeric value. The value {dict_['span']} could not be converted to a positive numeric value.")
				
				freq_range = np.array((center-span/2, center+span/2))
				
			elif "start" in dict_ and "stop" in dict_:
				try:
					start = pfloat(dict_["start"])
				except ValueError as E:
					raise ValueError(f"The start value has to be a positive numeric value. The value {dict_['start']} could not be converted to a positive numeric value.")
				
				try:
					stop = pfloat(dict_["stop"])
				except ValueError as E:
					raise ValueError(f"The stop value has to be a positive numeric value. The value {dict_['stop']} could not be converted to a positive numeric value.")
				
				freq_range = np.array((start, stop))
				center, span = (start + stop)/2, stop - start
			
			else:
				raise ValueError(f"The frequency range could not be determined. Please specify 'center' and 'span' or 'start' and 'stop'.")
			
			
			if "points" in dict_:
				try:
					points = max(1, int(dict_["points"]))
				except ValueError as E:
					raise ValueError(f"The points value has to be an integer value. The value {dict_['points']} could not be converted to an integer value.")
				
			elif "stepsize" in dict_:
				try:
					points = int((freq_range[1] - freq_range[0]) / (dict_["stepsize"] / 1000))
				except ValueError as E:
					raise ValueError(f"The stepsize value has to be a numeric value. The value {dict_['stepsize']} could not be converted to a numeric value.")

			direction = dict_["direction"]
			
			if direction == "forth":
				frequencies = np.linspace(*freq_range, points)
			elif direction == "back":
				frequencies = np.linspace(*freq_range[::-1], points)
			elif direction == "forthback":
				frequencies = np.concatenate((np.linspace(*freq_range, points), np.linspace(*freq_range[::-1], points)))
			elif direction == "backforth":
				frequencies = np.concatenate((np.linspace(*freq_range[::-1], points), np.linspace(*freq_range, points)))
			elif direction == "fromcenter":
				tmp_ltr = np.linspace(center, range_[1], int(points/2))
				tmp_rtl = np.linspace(center, range_[0], int(points/2))
				frequencies = np.empty((tmp_ltr.size + tmp_rtl.size -1), dtype=tmp_ltr.dtype)
				frequencies[0::2] = tmp_ltr
				frequencies[1::2] = tmp_rtl[1:]

		super().__init__(**dict_, **kwargs)
		self.frequencies = frequencies

class Cdeque(deque):
	def __init__(self, *args, onchange=print, **kwargs):
		self.onchange = onchange

	def popleft(self, *args):
		value = super().popleft(*args)
		self.onchange(self)
		return(value)

	def append(self, *args):
		super().append(*args)
		self.onchange(self)

	def appendleft(self, *args):
		super().appendleft(*args)
		self.onchange(self)

	def insert(self, *args):
		super().insert(*args)
		self.onchange(self)

	def clear(self, *args):
		super().clear(*args)
		self.onchange(self)
	
	def __delitem__(self, *args, **kwargs):
		super().__delitem__(*args)
		if not kwargs.get("silent"):
			self.onchange(self)

class Measurement(dict):
	def __init__(self, dict_):
		self.mode = dict_.get("general_mode")
		modes = devices.modes
		if self.mode not in modes:
			raise CustomValueError(f"The parameter 'general_mode' has to be in {modes} but is {self.mode}")

		self.readpressure = dict_.get("refill_measurepressure")
		self.sendnotification = dict_.get("general_sendnotification")
		self.refillcell = dict_.get("refill_refill")
		
		checktype_dict = {
			"general_mode*":					str,
			"static_probeaddress*":				str,
			"static_probedevice*":				str,
			"static_probemultiplication*":		pint,
			"static_lockinaddress*":			str,
			"static_lockindevice*":				str,
			"probe_frequency*":					Sweep,
			"probe_power*":						pint,
			"lockin_fmfrequency*":				pfloat,
			"lockin_fmamplitude*":				pfloat,
			"lockin_timeconstant*":				str,
			"lockin_delaytime*":				pfloat,
			"lockin_sensitivity*":				str,
			"lockin_acgain*":					str,
			"lockin_iterations*":				pint,
			
			"general_user": 					str,
			"general_molecule": 				str,
			"general_chemicalformula": 			str,
			"general_comment": 					str,
			"general_project": 					str,
			"general_sendnotification": 		bool,
			"refill_measurepressure": 			bool,
		}
		
		if (self.mode != "classic"):
			checktype_dict.update({
				"static_pumpaddress*":			str,
				"static_pumpdevice*":			str,
				"static_pumpmultiplication*":	pint,
				"pump_frequency*":				Sweep,
				"pump_power*":					pint,
			})
		
		if (self.mode == "dmdr"):
			checktype_dict.update({
				"general_dmjump*":				pfloat,
				"general_dmperiod*":			pfloat,
			})
		
		if self.readpressure:
			checktype_dict.update({"refill_address*":	str,})
		else:
			checktype_dict.update({"general_manualpressure":	str,})
		
		if self.sendnotification:
			checktype_dict.update({"general_notificationaddress*":	str,})
		
		if self.refillcell:
			checktype_dict.update({
				"refill_address*": 				str,
				"refill_inletaddress*": 		str,
				"refill_outletaddress*": 		str,
				"refill_minpressure*": 			pfloat,
				"refill_maxpressure*": 			pfloat,
				"refill_thresholdpressure*":	pfloat,
				"refill_emptypressure*": 		pfloat,
				"refill_force*": 				bool,
			})
		
		creation_dict_ = {}
		exceptions = []
		for key, class_ in checktype_dict.items():
			if key.endswith("*"):
				key = key[:-1]
				if key not in dict_:
					exceptions.append(f"No value was found for '{key}'.")
					continue
			else:
				if key not in dict_:
					continue
			
			try:
				creation_dict_[key] = class_(dict_[key])
			except ValueError as E:
				exceptions.append(str(E))
			
		if exceptions:
			raise CustomValueError( "\n".join(exceptions) )
		
		self.basic_information = None
		super().__init__(**creation_dict_)
	
	def run(self):
		self.lockin = None
		self.probe = None
		self.pump = None
		try:
			self.lockin = devices.connect(self, "lockin")
			self.probe  = devices.connect(self, "probe")
			if self.mode != "classic":
				self.pump   = devices.connect(self, "pump")

			if self.refillcell:
				rc, rm = mod_pumpcell.main({
					"pressure_min"				: self["refill_minpressure"] / 1e6,
					"pressure_max"				: self["refill_maxpressure"] / 1e6,
					"pressure_threshold"		: self["refill_thresholdpressure"] / 1e6,
					"pressure_empty"			: self["refill_emptypressure"] / 1e6,
					"pressure_gauge_port"		: self["refill_address"],
					"valve_inlet_port"			: self["refill_inletaddress"],
					"valve_outlet_port"			: self["refill_outletaddress"],
					"force"						: self["refill_force"],
				})
				
				if rc < 1:
					raise CustomError(rm)

			self.pressure_wrapper("general_pressurestart")
			self["general_datestart"] = str(datetime.now())[:19]
			
			self.spectrum_loop()
			
			self["general_dateend"] = str(datetime.now())[:19]
			self.pressure_wrapper("general_pressureend")

			self.save()
			
			if self.sendnotification:
				address = self["general_notificationaddress"]
				# @Luis: implement notification here
				# sendnotification("Measurement finished successfully", f"The measurement finished without any errors.")
		
		except Exception as E:
			if self.sendnotification:
				address = self["general_notificationaddress"]
				# @Luis: implement notification here
				# sendnotification("Measurement led to error", f"The execution of the measurement led to the following error:\n{E}")
			raise
		
		finally:
			for device in (self.lockin, self.probe, self.pump):
				if device:
					device.close()
	
	def spectrum_loop(self):
		shm = None
		try:
			probe_frequencies = self["probe_frequency"].frequencies
			probe_iterations = self["probe_frequency"]["iterations"]
			
			if (self.mode == "classic"):
				pump_frequencies = [0]
				pump_iterations = 1
			else:
				pump_frequencies = self["pump_frequency"].frequencies
				pump_iterations = self["pump_frequency"]["iterations"]
				
				if self.mode == "digital_dmdr":
					self.lockin.pump = self.pump
			
			# @Luis: Maybe change for loops to while loops -> allows to change iterations while running
			point_iterations = self["lockin_iterations"]
			
			n_probe, n_pump = len(probe_frequencies), len(pump_frequencies)
			
			n_total = pump_iterations * n_pump * probe_iterations * n_probe * point_iterations
			size = n_total * 8 * 3
			shape = (n_total, 3)
			shm = shared_memory.SharedMemory(create=True, size=size)
			result = np.ndarray(shape=shape, buffer=shm.buf, dtype=np.float64)
			result[:] = np.nan

			self.basic_information = {"action": "measurement", "size": size, "name": shm.name, "shape": shape, "time": self.lockin.dttc * n_total}
			server.send_all(self.basic_information)

			row = 0
			for pump_iteration in range(pump_iterations):
				for pump_index in range(n_pump):
					pump_frequency = pump_frequencies[pump_index]
					if pump_frequency:
						self.pump.set_frequency(pump_frequency)
					for probe_iteration in range(probe_iterations):
						for probe_index in range(n_probe):
							probe_frequency = probe_frequencies[probe_index]
							self.probe.set_frequency(probe_frequency)
							for point_iteration in range(point_iterations):

								intensity = self.lockin.measure_intensity()
								result[row] = probe_frequency, pump_frequency, intensity
								row += 1

								while experiment.state != "running":
									if experiment.state == "aborting":
										raise UserAbort("__ABORTING__")
									
									with experiment.nextfrequency_lock:
										if experiment.nextfrequency: 
											experiment.nextfrequency = False
											break
									
									time.sleep(0.1)

			self.result = result.copy()
			self.aborted = False

		except UserAbort as E:
			# Aborting can lead to different number of occurences for probe- and pump-frequency pairs
			result = result[~np.isnan(result[:, 0])]
			self.result = result.copy()
			self.aborted = True

		except Exception as E:
			raise
		
		finally:
			if shm:
				shm.close()
				shm.unlink()

	def pressure_wrapper(self, key):
		if self.readpressure:
			tmp = mod_pumpcell.measure_pressure_wrapper(self["refill_address"])
			if isinstance(tmp, Exception):
				server.send_all({"action": "error", "error": f"Could not read pressure. Error reads {tmp}."})
				self[key] = None
			else:
				self[key] = tmp

	def save(self):
		directory = os.path.join(homefolder, f"../../measurements_data/{self.get('general_molecule', 'Unknown')}/{str(datetime.now())[:10]}")
		if not os.path.exists(directory):
			os.makedirs(directory, exist_ok=True)
		
		self.save_spectrum(directory)
		# @Luis: add here database ...

	def save_spectrum(self, directory):
		result = self.result
		result_df = pd.DataFrame(result, columns=["probe", "pump", "intensity"])
		pivot_df = result_df.pivot_table(index="probe", columns="pump", values="intensity")

		intensities = pivot_df.values
		probe_frequencies = pivot_df.index.values
		pump_frequencies = pivot_df.columns.values
		
		probe = (probe_frequencies.min() + probe_frequencies.max())/2
		
		for column, pump in zip(intensities.T, pump_frequencies):
			tmp = f"_Pump@{pump:.2f}" if pump else ""
			tmp2 = f"_ABORTED" if self.aborted else ""
			filename = f"Probe@{probe:.2f}{tmp}{tmp2}_{self['general_datestart'].replace(':', '-')}"
			filename = os.path.realpath(f"{directory}/{filename}")
			np.savetxt(f"{filename}.dat", np.array((probe_frequencies, column)).T, delimiter="\t")
			self.save_meta(filename)

	def save_meta(self, filename):
		if self.aborted:
			self["general_aborted"] = True
		
		output_dict = {}
		for key, value in self.items():
			category, name = key.split("_", 1)
			category = category.capitalize()
			if category not in output_dict:
				output_dict[category] = {}
			if type(value) in (dict, list, tuple):
				value = json.dumps(value)

			output_dict[category][name] = value

		config_parser = configparser.ConfigParser(interpolation=None)
		for section in output_dict:
			config_parser.add_section(section)
			for key in output_dict[section]:
				config_parser.set(section, key, str(output_dict[section][key]))

		with open(f"{filename}.ini", "w+", encoding="utf-8") as file:
			config_parser.write(file)

class Experiment():
	def __init__(self):
		self._state = "ready"
		self.send_all = print
		self.queue = Cdeque(onchange=lambda queue: self.send_all({"action": "queue", "data": list(queue)}))
		
		self.queue_lock = threading.Lock()
		self.current_measurement = None
		self.thread = None
		
		self.nextfrequency = 0
		self.nextfrequency_lock = threading.Lock()

		self.spectrum = {
			"ranges": {
				"probe": (0, 100),
				"pump": (0, 100),
			},
			"xaxis": "probe",
			"sort": False,
		}
		self.data = {
			"probe": [],
			"pump": [],
			"intensity": [],
			"time": [],
		}

	@property
	def state(self):
		return self._state

	@state.setter
	def state(self, value):
		send_message = (self.state != value)
		self._state = value
		if send_message:
			self.send_all({
				"action": "state",
				"state": self.state,
			})

	def loop(self):
		while True:
			try:
				self.current_measurement = self.queue.popleft()
				self.state = "running"
				self.current_measurement.run()
			except IndexError:
				self.state = "waiting"
				time.sleep(0.2)
			except (CustomValueError, CustomError) as E:
				self.send_all({
					"action": "error",
					"error": f"{E}"
				})
			except Exception as E:
				self.send_all({
					"action": "uerror",
					"error": f"An error occurred while performing a measurement:\n{E}\n{traceback.format_exc()}"
				})
				stderr.write(f"An error occurred while performing a measurement:\n{E}\n{traceback.format_exc()}")

	def start(self):
		self.thread = threading.Thread(target=self.loop, args=[])
		self.thread.daemon = True
		self.thread.start()

	def add_measurement_last(self, measurement):
		self.queue.append(Measurement(measurement))

	def add_measurement_first(self, measurement):
		self.queue.appendleft(Measurement(measurement))
	
	def add_measurement_now(self, measurement):
		self.queue.appendleft(Measurement(measurement))
		self.state = "aborting"
	
	def del_measurements(self, indices=None):
		if indices is None:
			self.queue.clear()
		else:
			for index in sorted(indices, reverse=True):
				del self.queue[index]
		
	def add_measurements(self, measurement_dicts):
		errors = {}
		measurements = []
		for i, measurement in enumerate(measurement_dicts):
			try:
				measurements.append(Measurement(measurement))
			except (CustomError, CustomValueError) as E:
				errors[i] = str(E)
		
		if errors:
			raise CustomError(f"There were {len(errors)} measurements with errors. The errors read: {errors}.")
		else:
			for measurement in measurements:
				self.queue.append(measurement)
	
	def reorder_measurement(self, oldindex, newindex):
		tmp = self.queue[oldindex]
		self.queue.__delitem__(oldindex, silent=True)
		self.queue.insert(newindex, tmp)

class Websocket():
	def __init__(self, experiment):
		self.server = websockets.serve(self.main, URL, PORT)
		self.loop = asyncio.get_event_loop()
		self.listeners = set()
		self.experiment = experiment
		experiment.send_all = self.send_all
	
	def start(self):
		self.loop.run_until_complete(self.server)
		self.loop.run_forever()
		
	def send_all(self, dict_):
		asyncio.run_coroutine_threadsafe(self.send_all_core(dict_), self.loop)
		
	async def send_all_core(self, dict_):
		try:
			await asyncio.gather(*(listener.send(json.dumps(dict_)) for listener in self.listeners))
		except Exception as E:
			print(E)
			raise E
		
	async def main(self, websocket, path):
		try:
			self.listeners.add(websocket)
			await websocket.send(json.dumps({"action": "state", "state": self.experiment.state}))
			await websocket.send(json.dumps(({"action": "queue", "data": list(experiment.queue)})))
			
			if self.experiment.current_measurement and self.experiment.current_measurement.basic_information:
				await websocket.send(json.dumps(self.experiment.current_measurement.basic_information))
		
			async for message in websocket:
				try:
					output = {}
					message = json.loads(message)
					action  = message.get("action")
					
					if action == "state":
						self.experiment.state = message.get("state")
					
					elif action == "add_measurement_last":
						self.experiment.add_measurement_last(message.get("measurement"))
					
					elif action == "add_measurement_first":
						self.experiment.add_measurement_first(message.get("measurement"))
					
					elif action == "add_measurement_now":
						self.experiment.add_measurement_now(message.get("measurement"))
					
					elif action == "del_measurements":
						self.experiment.del_measurements()
					
					elif action == "del_measurement":
						self.experiment.del_measurements(message.get("indices"))
					
					elif action == "reorder_measurement":
						self.experiment.reorder_measurement(message.get("oldindex"), message.get("newindex"))
					
					elif action == "add_measurements":
						self.experiment.add_measurements(message.get("measurements"))
					
					elif action == "next_frequency":
						with self.experiment.nextfrequency_lock:
							self.experiment.nextfrequency = True
					
					elif action == "kill":
						await asyncio.gather(*[listener.close() for listener in self.listeners])
						exit()
					
					else:
						raise CustomError(f"The request with action '{action}' was not understood.")
				
				except (CustomError, CustomValueError) as E:
					output = {
						"action": "error",
						"error": str(E),
					}
				
				except Exception as E:
					output = {
						"action": "uerror",
						"error": str(E),
					}
				
				finally:
					if output:
						self.send_all(output)
				
		except Exception as E:
			raise
		
		finally:
			self.listeners.remove(websocket)
	
if __name__ == "__main__":
	stdout = Tee(homefolder + "/../logs/EXPERIMENT.txt", "a+", False)
	stderr = Tee(homefolder + "/../logs/EXPERIMENT.err", "a+", True)
	
	experiment = Experiment()
	server = Websocket(experiment)
	
	experiment.start()
	server.start()
