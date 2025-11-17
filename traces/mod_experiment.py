#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Author: Luis Bonah

import os
import sys
import time
import json
import serial
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

from . import mod_devices as devices

URL, PORT = "localhost", 8112

homefolder = os.path.join(os.path.expanduser("~"), "TRACE")
os.makedirs(homefolder, exist_ok=True)

## Options for Initializing Serial Devices
kwargs_serial_gauge = {
	"baudrate": 2400,
	"timeout": 1,
}

## Translate units into mbar
pressure_translation = {
	"mbar": 1,
	"torr": 1.33322,
	"pa": 0.01,
	"micron": 0.001,
}


class Tee(object):
	def __init__(self, name, mode, target_stderr=False):
		self.file = open(name, mode)
		self._original_object = sys.stderr if target_stderr else sys.stdout
		self._target_stderr = target_stderr
	
		if target_stderr:
			sys.stderr = self
		else:
			sys.stdout = self

	def __del__(self):
		if self._target_stderr:
			sys.stderr = self._original_object
		else:
			sys.stdout = self._original_object
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
			return tmp
		else:
			raise ValueError(
				"The value of a pint has to be positive, the provided value was {tmp}."
			)


class pfloat(float):
	def __new__(cls, *args, **kwargs):
		tmp = super().__new__(cls, *args, **kwargs)
		if tmp >= 0:
			return tmp
		else:
			raise ValueError(
				f"The value of a pfloat has to be positive, the provided value was {tmp}."
			)


class Sweep(dict):
	_draft = {
		"mode": {"fixed", "sweep"},
		"direction": {
			"forth",
			"forthback",
			"back",
			"backforth",
			"fromcenter",
			"random",
		},
	}

	def __init__(self, dict_={}, **kwargs):
		dict_.update(**kwargs)

		if "iterations" not in dict_:
			dict_["iterations"] = 1
		else:
			try:
				dict_["iterations"] = int(dict_["iterations"])
			except ValueError as E:
				raise ValueError(
					f"The value for the iterations '{dict_['iterations']}' could not be converted to an integer."
				)

		key = "mode"
		if not key in dict_:
			raise ValueError(f"The Sweep object is missing the '{key}' parameter.")
			if dict_[key] not in self._draft[key]:
				raise ValueError(
					f"The argument '{dict_[key]}' for the '{key}' parameter is not understood. Please use one of the following values: {_draft[key]}"
				)

		if dict_["mode"] != "fixed":
			key = "direction"
			if not key in dict_:
				raise ValueError(f"The Sweep object is missing the '{key}' parameter.")
				if dict_[key] not in self._draft[key]:
					raise ValueError(
						f"The argument '{dict_[key]}' for the '{key}' parameter is not understood. Please use one of the following values: {_draft[key]}"
					)

		if dict_["mode"] == "fixed":
			try:
				center = pfloat(dict_["center"])
			except ValueError as E:
				raise ValueError(
					f"The center value has to be a positive numeric value. The value {dict_['center']} could not be converted to a positive numeric value."
				)

			frequencies = lambda: np.array((center,))
			init_frequency = center

		else:

			if "center" in dict_ and "span" in dict_:
				try:
					center = pfloat(dict_["center"])
				except ValueError as E:
					raise ValueError(
						f"The center value has to be a positive numeric value. The value {dict_['center']} could not be converted to a positive numeric value."
					)

				try:
					span = pfloat(dict_["span"])
				except ValueError as E:
					raise ValueError(
						f"The span value has to be a positive numeric value. The value {dict_['span']} could not be converted to a positive numeric value."
					)

				freq_range = np.array((center - span / 2, center + span / 2))

			elif "start" in dict_ and "stop" in dict_:
				try:
					start = pfloat(dict_["start"])
				except ValueError as E:
					raise ValueError(
						f"The start value has to be a positive numeric value. The value {dict_['start']} could not be converted to a positive numeric value."
					)

				try:
					stop = pfloat(dict_["stop"])
				except ValueError as E:
					raise ValueError(
						f"The stop value has to be a positive numeric value. The value {dict_['stop']} could not be converted to a positive numeric value."
					)

				freq_range = np.array((start, stop))
				center, span = (start + stop) / 2, stop - start

			else:
				raise ValueError(
					f"The frequency range could not be determined. Please specify 'center' and 'span' or 'start' and 'stop'."
				)

			if "points" in dict_:
				try:
					points = max(1, int(dict_["points"]))
				except ValueError as E:
					raise ValueError(
						f"The points value has to be an integer value. The value {dict_['points']} could not be converted to an integer value."
					)

			elif "stepsize" in dict_:
				try:
					points = int(
						(freq_range[1] - freq_range[0]) / (dict_["stepsize"] / 1000)
					)
				except ValueError as E:
					raise ValueError(
						f"The stepsize value has to be a numeric value. The value {dict_['stepsize']} could not be converted to a numeric value."
					)

			direction = dict_["direction"]

			if direction == "forth":
				frequencies = lambda freq_range=freq_range, points=points: np.linspace(
					*freq_range, points
				)
				init_frequency = freq_range[0]
			elif direction == "back":
				frequencies = lambda freq_range=freq_range, points=points: np.linspace(
					*freq_range[::-1], points
				)
				init_frequency = freq_range[1]
			elif direction == "forthback":
				frequencies = (
					lambda freq_range=freq_range, points=points: np.concatenate(
						(
							np.linspace(*freq_range, points),
							np.linspace(*freq_range[::-1], points),
						)
					)
				)
				init_frequency = freq_range[0]
			elif direction == "backforth":
				frequencies = (
					lambda freq_range=freq_range, points=points: np.concatenate(
						(
							np.linspace(*freq_range[::-1], points),
							np.linspace(*freq_range, points),
						)
					)
				)
				init_frequency = freq_range[1]
			elif direction == "fromcenter":

				def tmp(center, freq_range, points):
					tmp_ltr = np.linspace(center, freq_range[1], int(points / 2))
					tmp_rtl = np.linspace(center, freq_range[0], int(points / 2))
					frequencies = np.empty(
						(tmp_ltr.size + tmp_rtl.size - 1), dtype=tmp_ltr.dtype
					)
					frequencies[0::2] = tmp_ltr
					frequencies[1::2] = tmp_rtl[1:]
					return frequencies

				frequencies = (
					lambda freq_range=freq_range, points=points, center=center: tmp(
						center, freq_range, points
					)
				)
				init_frequency = center

		super().__init__(**dict_, **kwargs)
		self.frequencies = frequencies
		self.init_frequency = init_frequency


class Cdeque(deque):
	def __init__(self, *args, onchange=print, **kwargs):
		self.onchange = onchange

	def popleft(self, *args):
		value = super().popleft(*args)
		self.onchange(self)
		return value

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

	def extend(self, *args):
		super().extend(*args)
		self.onchange(self)


class Measurement(dict):
	def __init__(self, dict_):
		self.mode = dict_.get("general_mode")
		modes = devices.modes
		if self.mode not in modes:
			raise CustomValueError(
				f"The parameter 'general_mode' has to be in {modes} but is {self.mode}"
			)

		self.sendnotification = dict_.get("general_sendnotification")

		checktype_dict = {
			"general_mode*": str,
			"static_probeaddress*": str,
			"static_probedevice*": str,
			"static_probemultiplication*": pint,
			"static_lockinaddress*": str,
			"static_lockindevice*": str,
			"static_skipreset*": bool,
			"static_pressuregaugaaddress": str,
			"probe_frequency*": Sweep,
			"probe_power*": pint,
			"lockin_fmfrequency*": pfloat,
			"lockin_fmdeviation*": pfloat,
			"lockin_timeconstant*": str,
			"lockin_delaytime*": pfloat,
			"lockin_sensitivity*": str,
			"lockin_acgain*": str,
			"lockin_iterations*": pint,
			"general_user": str,
			"general_molecule": str,
			"general_chemicalformula": str,
			"general_comment": str,
			"general_project": str,
			"general_sendnotification": bool,
		}

		if self.mode != "classic":
			checktype_dict.update(
				{
					"static_pumpaddress*": str,
					"static_pumpdevice*": str,
					"static_pumpmultiplication*": pint,
					"pump_frequency*": Sweep,
					"pump_power*": pint,
				}
			)

		if self.mode == "dmdr":
			checktype_dict.update(
				{
					"general_dmjump*": pfloat,
					"general_dmperiod*": pfloat,
				}
			)

		if self.sendnotification:
			checktype_dict.update(
				{
					"general_notificationaddress*": str,
				}
			)

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
			raise CustomValueError("\n".join(exceptions))

		self.basic_information = None
		super().__init__(**creation_dict_)

	def run(self):
		self.lockin = None
		self.probe = None
		self.pump = None
		try:
			self.probe = devices.connect(self, "probe")
			if self.mode != "classic":
				self.pump = devices.connect(self, "pump")
			self.lockin = devices.connect(self, "lockin")

			self["general_pressurestart"] = self.measure_pressure()
			self["general_datestart"] = str(datetime.now())[:19]

			self.spectrum_loop()

			self["general_dateend"] = str(datetime.now())[:19]
			self["general_pressureend"] = self.measure_pressure()

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
			delay_time = self["lockin_delaytime"] / 1000

			probe_frequencies = self["probe_frequency"].frequencies()
			probe_iterations = self["probe_frequency"]["iterations"]

			if self.mode == "classic":
				pump_frequencies = [0]
				pump_iterations = 1
			else:
				pump_frequencies = self["pump_frequency"].frequencies()
				pump_iterations = self["pump_frequency"]["iterations"]

				if self.mode == "digital_dmdr":
					self.lockin.pump = self.pump

			# @Luis: Maybe change for loops to while loops -> allows to change iterations while running
			point_iterations = self["lockin_iterations"]

			n_probe, n_pump = len(probe_frequencies), len(pump_frequencies)

			n_total = (
				pump_iterations * n_pump * probe_iterations * n_probe * point_iterations
			)
			values_per_point = 4
			bytes_per_float = 8
			size = n_total * bytes_per_float * values_per_point
			shape = (n_total, values_per_point)
			shm = shared_memory.SharedMemory(create=True, size=size)
			result = np.ndarray(shape=shape, buffer=shm.buf, dtype=np.float64)
			result[:] = np.nan

			time_estimate = (delay_time * n_total / probe_iterations) + (
				self.lockin.timeconstant * n_total
			)
			self.basic_information = {
				"action": "measurement",
				"size": size,
				"name": shm.name,
				"shape": shape,
				"time": time_estimate,
			}
			server.send_all(self.basic_information)

			row = 0
			for _ in range(pump_iterations):
				for pump_index in range(n_pump):
					pump_frequency = pump_frequencies[pump_index]
					if pump_frequency:
						self.pump.set_frequency(pump_frequency)
					for _ in range(probe_iterations):
						for probe_index in range(n_probe):
							probe_frequency = probe_frequencies[probe_index]
							self.probe.set_frequency(probe_frequency)

							# Wait delay time before measuring anything
							counterstart = time.perf_counter()
							while time.perf_counter() - counterstart < delay_time:
								continue

							counterend = time.perf_counter()
							if counterend - counterstart > delay_time + 0.05:
								print(counterend - counterstart - delay_time)

							for _ in range(point_iterations):

								x, y = self.lockin.measure_intensity()
								result[row] = probe_frequency, pump_frequency, x, y
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

	def measure_pressure(self):
		address = self["static_pressuregaugaaddress"]

		if not address.strip():
			return None

		try:
			device = serial.Serial(port=address, **kwargs_serial_gauge)

			command = "MES R TM2\r\n"
			command = command.encode("utf-8")
			device.write(command)
			response = device.readline().decode("utf-8")

			if not response:
				server.send_all(
					{
						"action": "error",
						"error": f"Could not read pressure. Error reads {tmp}.",
					}
				)
				return None

			_, unit, value = response.split(":")[:3]
			unit_factor = pressure_translation[unit.lower().strip()]
			pressure = np.float64(value) * unit_factor
			return pressure / 1000

		except Exception as E:
			server.send_all(
				{
					"action": "error",
					"error": f"Could not read pressure. Error reads {tmp}.",
				}
			)
			return None

	def save(self):
		directory = os.path.join(homefolder, "data", str(datetime.now())[:10])
		if not os.path.exists(directory):
			os.makedirs(directory, exist_ok=True)

		self.save_spectrum(directory)
		# @Luis: add here database ...

	def save_spectrum(self, directory):
		result = self.result
		result_df = pd.DataFrame(result, columns=["probe", "pump", "x", "y"])
		pivot_df_x = result_df.pivot_table(
			index="probe", columns="pump", values="x", sort=True
		)
		pivot_df_y = result_df.pivot_table(
			index="probe", columns="pump", values="y", sort=True
		)

		intensities_x = pivot_df_x.values
		intensities_y = pivot_df_y.values
		probe_frequencies = pivot_df_x.index.values
		pump_frequencies = pivot_df_x.columns.values

		probe = (probe_frequencies.min() + probe_frequencies.max()) / 2

		for column_x, column_y, pump in zip(
			intensities_x.T, intensities_y.T, pump_frequencies
		):
			tmp = f"_Pump@{pump:.2f}" if pump else ""
			tmp2 = f"_ABORTED" if self.aborted else ""
			filename = f"Probe@{probe:.2f}{tmp}{tmp2}_{self['general_datestart'].replace(':', '-')}"
			filename = os.path.realpath(f"{directory}/{filename}")
			np.savetxt(
				f"{filename}.dat",
				np.array((probe_frequencies, column_x, column_y)).T,
				delimiter="\t",
			)
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


class Experiment:
	def __init__(self):
		self._state = "ready"
		self._pause_after_abort = False
		self.send_all = print
		self.queue = Cdeque(
			onchange=lambda queue: self.send_all(
				{"action": "queue", "data": list(queue)}
			)
		)

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
		send_message = self.state != value
		self._state = value
		if send_message:
			self.send_all(
				{
					"action": "state",
					"state": self.state,
				}
			)

	@property
	def pause_after_abort(self):
		return self._pause_after_abort

	@pause_after_abort.setter
	def pause_after_abort(self, value):
		self._pause_after_abort = value

		self.send_all(
			{
				"action": "pause_after_abort",
				"state": self.pause_after_abort,
			}
		)

	def loop(self):
		while True:
			try:
				wait_for_user_confirmation = self.pause_after_abort or (
					self.state == "deviceerror"
				)
				if wait_for_user_confirmation:
					while self.state in ["aborting", "deviceerror"]:
						time.sleep(0.1)

				self.current_measurement = self.queue.popleft()
				self.state = "running"
				self.current_measurement.run()
			except IndexError:
				self.state = "waiting"
				time.sleep(0.2)
			except (CustomValueError, CustomError) as E:
				self.send_all({"action": "error", "error": f"{E}"})
			except devices.DeviceError as E:
				self.send_all({"action": "error", "error": f"{E}"})
				self.state = "deviceerror"
				stderr.write(
					f"An error occurred while performing a measurement:\n{E}\n{traceback.format_exc()}"
				)

			except Exception as E:
				self.send_all(
					{
						"action": "uerror",
						"error": f"An error occurred while performing a measurement:\n{E}\n{traceback.format_exc()}",
					}
				)
				stderr.write(
					f"An error occurred while performing a measurement:\n{E}\n{traceback.format_exc()}"
				)

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
			raise CustomError(
				f"There were {len(errors)} measurements with errors. The errors read: {errors}."
			)
		else:
			self.queue.extend(measurements)

	def reorder_measurement(self, oldindex, newindex):
		tmp = self.queue[oldindex]
		self.queue.__delitem__(oldindex, silent=True)
		self.queue.insert(newindex, tmp)

	def pop_measurement(self):
		current_measurement = self.current_measurement
		if current_measurement:
			self.add_measurement_first(current_measurement)


class Websocket:
	def __init__(self, experiment):
		self.listeners = set()
		self.experiment = experiment
		experiment.send_all = self.send_all
		self.loop = None

	async def start(self):
		self.server = await websockets.serve(self.main, URL, PORT)
		self.loop = self.server.get_loop()
		await self.server.serve_forever()

	def send_all(self, dict_):
		while not self.loop:
			time.sleep(1)
		asyncio.run_coroutine_threadsafe(self.send_all_core(dict_), self.loop)

	async def send_all_core(self, dict_):
		try:
			await asyncio.gather(
				*(listener.send(json.dumps(dict_)) for listener in self.listeners)
			)
		except Exception as E:
			print(E)
			raise E

	async def main(self, websocket):
		try:
			self.listeners.add(websocket)
			await websocket.send(
				json.dumps({"action": "state", "state": self.experiment.state})
			)
			await websocket.send(
				json.dumps({"action": "queue", "data": list(experiment.queue)})
			)
			await websocket.send(
				json.dumps(
					{
						"action": "pause_after_abort",
						"state": self.experiment.pause_after_abort,
					}
				)
			)

			if (
				self.experiment.current_measurement
				and self.experiment.current_measurement.basic_information
			):
				await websocket.send(
					json.dumps(self.experiment.current_measurement.basic_information)
				)

			async for message in websocket:
				try:
					output = {}
					message = json.loads(message)
					action = message.get("action")

					if action == "state":
						self.experiment.state = message.get("state")

					elif action == "add_measurement_last":
						self.experiment.add_measurement_last(message.get("measurement"))

					elif action == "add_measurement_first":
						self.experiment.add_measurement_first(
							message.get("measurement")
						)

					elif action == "add_measurement_now":
						self.experiment.add_measurement_now(message.get("measurement"))

					elif action == "del_measurements":
						self.experiment.del_measurements()

					elif action == "del_measurement":
						self.experiment.del_measurements(message.get("indices"))

					elif action == "reorder_measurement":
						self.experiment.reorder_measurement(
							message.get("oldindex"), message.get("newindex")
						)

					elif action == "add_measurements":
						self.experiment.add_measurements(message.get("measurements"))

					elif action == "next_frequency":
						with self.experiment.nextfrequency_lock:
							self.experiment.nextfrequency = True

					elif action == "pop_measurement":
						self.experiment.pop_measurement()

					elif action == "pause_after_abort":
						self.experiment.pause_after_abort = (
							not self.experiment.pause_after_abort
						)

					elif action == "measurements_folder":
						folder = os.path.join(homefolder, "data")
						await websocket.send(
							json.dumps(
								{"action": "measurements_folder", "folder": folder}
							)
						)

					elif action == "kill":
						await asyncio.gather(
							*[listener.close() for listener in self.listeners]
						)
						exit()

					else:
						raise CustomError(
							f"The request with action '{action}' was not understood."
						)

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


def start():
	global experiment
	global server
	global stderr
	global stdout

	log_folder = os.path.join(homefolder, "logs")
	os.makedirs(log_folder, exist_ok=True)
	stdout = Tee(os.path.join(log_folder, "EXPERIMENT.txt"), "a+", False)
	stderr = Tee(os.path.join(log_folder, "EXPERIMENT.err"), "a+", True)

	experiment = Experiment()
	server = Websocket(experiment)

	experiment.start()
	asyncio.run(server.start())


if __name__ == "__main__":
	start()
