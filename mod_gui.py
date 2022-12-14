#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Author: Luis Bonah
# Description: Measurement Software

CREDITSSTRING = """Made by Luis Bonah

As this programs GUI is based on PyQt5, which is GNU GPL v3 licensed, this program is also licensed under GNU GPL v3 (See the bottom paragraph).

pandas, matplotlib, scipy and numpy were used for this program, speeding up the development process massively.

Copyright (C) 2020

	This program is free software: you can redistribute it and/or modify
	it under the terms of the GNU General Public License as published by
	the Free Software Foundation, either version 3 of the License, or
	(at your option) any later version.

	This program is distributed in the hope that it will be useful,
	but WITHOUT ANY WARRANTY; without even the implied warranty of
	MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
	GNU General Public License for more details.

	You should have received a copy of the GNU General Public License
	along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""

##
## Global Constants and Imports
##
APP_TAG = "TRACE"

import os
import sys
import re
import io
import time
import copy
import wrapt
import random
import json
import queue
import threading
import websocket
import configparser
import traceback as tb
import numpy as np
import pandas as pd
import subprocess
import webbrowser
import pyckett

from multiprocessing import shared_memory
from scipy import optimize, special, signal

from PyQt5.QtCore import *
from PyQt5.QtWidgets import *
from PyQt5.QtGui import *

import matplotlib
from matplotlib import style, figure
from matplotlib.backends.backend_qt5agg import FigureCanvas, NavigationToolbar2QT

import warnings
warnings.simplefilter('ignore', np.RankWarning)

QLocale.setDefault(QLocale("en_EN"))

homefolder = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, homefolder)
import mod_devices as devices


##
## Global Decorators
##
def stopwatch_d(func):
	def timed(*args, **kwargs):
		start_time = time.time()
		result = func(*args, **kwargs)
		stop_time = time.time()
		print(f"Executing {func.__name__} took {stop_time-start_time}.")
		return result
	return timed

def askfilesfirst_d(func):
	def tmp(*args, **kwargs):
		if kwargs.get("add_files") == True:
			kwargs["add_files"] = QFileDialog.getOpenFileNames(None, f'Choose {args[1].capitalize()} File(s)',)[0]
			if len(kwargs["add_files"]) == 0:
				return
		return(func(*args, **kwargs))
	return tmp

def threading_d(func):
	def run(*args, **kwargs):
		t = threading.Thread(target=func, args=args, kwargs=kwargs)
		t.start()
		return t
	return run

def synchronized_d(lock):
	@wrapt.decorator
	def _wrapper(wrapped, instance, args, kwargs):
		with lock:
			return wrapped(*args, **kwargs)
	return _wrapper

def working_d(func):
	def wrapper(self, *args, **kwargs):
		queue_ = mw.plotwidget.working
		queue_.put(1)
		if not queue_.empty():
			mw.signalclass.setindicator.emit("<span style='font-weight: 600;'>Working...</span>")

		try:
			return(func(self, *args, **kwargs))
		except Exception as E:
			raise
		finally:
			queue_.get()
			queue_.task_done()
			if queue_.empty():
				mw.signalclass.setindicator.emit("Ready")
	return(wrapper)

locks = {key: threading.RLock() for key in ("exp_df", "cat_df", "lin_df", "windows", "currThread", "axs", "meas")}


class MainWindow(QMainWindow):
	def __init__(self, parent=None):
		global mw
		mw = self
		
		super().__init__(parent)
		self.setFocusPolicy(Qt.StrongFocus)
		self.setWindowTitle(APP_TAG)
		self.setAcceptDrops(True)

		try:
			app.setWindowIcon(QIcon(customfile(".svg")))
			import ctypes
			ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_TAG)
		except Exception as E:
			pass
		
		self.exp_df = pd.DataFrame(columns=exp_dtypes.keys()).astype(exp_dtypes)
		self.exp_df["filename"] = None
		self.cat_df = pd.DataFrame(columns=cat_dtypes.keys()).astype(cat_dtypes)
		self.cat_df["filename"] = None
		self.lin_df = pd.DataFrame(columns=lin_dtypes.keys()).astype(lin_dtypes)
		self.lin_df["filename"] = None
		
		self.signalclass = SignalClass()
		self.config = Config(self.signalclass.updateconfig)
		self.loadoptions()
		
		self.tabwidget = QTabWidget()
		self.setCentralWidget(self.tabwidget)
		
		self.plotwidget = PlotWidget(self)
		self.tabwidget.addTab(self.plotwidget, "Plot")
		
		self.measurementwidget = QWidget()
		self.measurementlayout = QGridLayout()
		self.measurementwidget.setLayout(self.measurementlayout)
		self.tabwidget.addTab(self.measurementwidget, "Measurements")
		
		self.notificationsbox = NotificationsBox()
		self.signalclass.notification.connect(lambda text: self.notificationsbox.add_message(text))
		
		self.queuewindow = QueueWindow(self)
		self.generalwindow = GeneralWindow(self)
		self.staticwindow = StaticWindow(self)
		self.probewindow = ProbeWindow(self)
		self.pumpwindow = PumpWindow(self)
		self.lockinwindow = LockInWindow(self)
		self.refillwindow = RefillWindow(self)
		self.hoverwindow = HoverWindow(self)
		self.hoverwindow.hide()
		self.configwindow = ConfigWindow(self)
		self.configwindow.hide()
		self.logwindow = LogWindow(self)
		
		self.tabifyDockWidget(self.logwindow, self.queuewindow)
		
		self.filewindow = FileWindow(self)
		self.creditswindow = CreditsWindow(self)
		
		self.statusbar = QStatusBar()
		self.setStatusBar(self.statusbar)

		self.progressbar = QProgressBar()
		self.statusbar.addWidget(self.progressbar, 2)
		self.signalclass.progressbar.connect(self.progressbar.setValue)
		
		self.timeindicator = QQ(QLabel, text="")
		self.statusbar.addWidget(self.timeindicator)
		
		self.stateindicator = QQ(QLabel, text="")
		self.statusbar.addWidget(self.stateindicator)
		

		self.statebuttons = {}
		for key, label in {"running": "Run", "pausing": "Pause", "aborting": "Abort"}.items():
			widget = QQ(QToolButton, text=label, change=lambda x, key=key: ws.send({"action": "state", "state": key}))
			self.statebuttons[key] = widget
			self.statusbar.addWidget(widget)
		
		self.update_state("disconnected")

		self.shortcuts()
		self.createmenu()
		self.show()
		
		self.signalclass.websocketaction.connect(self.websocketaction)

	@synchronized_d(locks["meas"])
	def closeEvent(self, event):
		
		try:
			ws.close()
			
			if self.plotwidget.shared_memory:
				self.plotwidget.shared_memory.close()
				self.plotwidget.meas_array = None
		except:
			pass
		
		self.logwindow.close()
		self.queuewindow.close()
		self.hoverwindow.close()
		self.configwindow.close()
		
		event.accept()
	
	def dragEnterEvent(self, event):
		if event.mimeData().hasUrls():
			event.accept()
		else:
			event.ignore()

	def dropEvent(self, event):
		files = [url.toLocalFile() for url in event.mimeData().urls()]
		files_dropped = {}

		types = mw.config["flag_extensions"]
		files_by_class = {key: [] for key in list(types.keys())}

		for file in files:
			if not os.path.isfile(file):
				mw.notification(f"<span style='color:#ff0000;'>ERROR</span>: The file {file} could not be found.")
				continue

			extension = os.path.splitext(file)[1]
			type = None
			for key, value in types.items():
				if extension in value:
					type = key
					break

			if type is None:
				item, ok = QInputDialog.getItem(self, "Choose File Type", f"Choose the file type for the extension \"{extension}\":", [x.capitalize() for x in types], editable=False)
				if not (ok and item):
					continue
				types[item.lower()].append(extension)
				type = item.lower()

			files_by_class[type].append(file)
		self.dropEvent_core(files_by_class)
	
	@threading_d
	def dropEvent_core(self, files_by_class):
		threads = []
		for type in ["exp", "cat", "lin"]:
			files = files_by_class[type]
			if files:
				threads.append(mw.load_file(type, keep_old=True, add_files=files, skip_update=True))

		for thread in threads:
			thread.join()

		for measurement in files_by_class["measurement"]:
			mw.loadmeasurement(measurement)

		mw.plotwidget.set_data()
	
	def websocketaction(self, message):
		action = message.get("action")
		
		if action == "measurement":
			name = message["name"]
			size = message["size"]
			shape = message["shape"]
			time = message["time"]
			
			self.plotwidget.connect_shared_memory(name, size, shape)
			self.timeindicator.setText(f"Est. Time: {time:.0f} s")
		
		elif action == "queue":
			queue = message["data"]
			self.queuewindow.update_queue(queue)
		
		elif action == "state":
			state = message["state"]
			self.update_state(state)

		elif action == "error":
			error = message["error"]
			self.notification(f"<span style='color:#eda711;'>EXPERIMENT WARNING</span>: {error}")
		
		elif action == "uerror":
			error = message["error"]
			self.notification(f"<span style='color:#ff0000;'>EXPERIMENT ERROR</span>: {error}")

		elif action == "connection_error":
			error = message["error"]
			self.update_state("disconnected")
			self.notification(f"<span style='color:#ff0000;'>CONNECTION ERROR</span>: {error}")
		
		elif action == "closed":
			self.update_state("disconnected")
			self.notification(f"<span style='color:#ff0000;'>CONNECTION CLOSED</span>")

		elif action == "opened":
			mw.notification(f"<span style='color:#29d93b;'>CONNECTION OPENED</span>")

		elif action == "pause_after_abort":
			state = message["state"]
			self.update_pause_after_abort(state)

		else:
			self.notification(f"<span style='color:#ff0000;'>ERROR</span>: Received a message with the unknown action '{action}' {message=}.")
		
		
		

	
	def change_style(self, style=None):
		styles = ["light", "dark", "custom"]
		if style == None:
			self.config["layout_theme"] = styles[(styles.index(self.config["layout_theme"])+1)%len(styles)]
		elif style in styles:
			self.config["layout_theme"] = style
		else:
			self.config["layout_theme"] = styles[0]

		if self.config["layout_owntheme"] == {} and self.config["layout_theme"] == "custom":
			self.config["layout_theme"] = "light"

		if self.config["layout_theme"] == "light":
			palette = app.style().standardPalette()
			mplstyles = ("default", "white")

		elif self.config["layout_theme"] == "dark" or self.config["layout_theme"] == "custom":
			colors = {
				"window":				QColor(53, 53, 53),
				"windowText":			QColor(255, 255, 255),
				"base":					QColor(35, 35, 35),
				"alternateBase":		QColor(53, 53, 53),
				"toolTipBase":			QColor(25, 25, 25),
				"toolTipText":			QColor(255, 255, 255),
				"placeholderText":		QColor(100, 100, 100),
				"text":					QColor(255, 255, 255),
				"button":				QColor(53, 53, 53),
				"buttonText":			QColor(255, 255, 255),
				"brightText":			Qt.red,
				"light":				QColor(255, 255, 255),
				"midlight":				QColor(200, 200, 200),
				"mid":					QColor(150, 150, 150),
				"dark":					QColor(50, 50, 50),
				"shadow":				QColor(0, 0, 0),
				"highlight":			QColor(42, 130, 218),
				"highlightedText":		 QColor(35, 35, 35),
				"link":					QColor(42, 130, 218),
				"linkVisited":			QColor(42, 130, 218),

				"disabledButtonText":	Qt.darkGray,
				"disabledWindowText":	Qt.darkGray,
				"disabledText":			Qt.darkGray,
				"disabledLight":		QColor(53, 53, 53),

				"mplstyles":			("dark_background", "black"),
			}

			if self.config["layout_theme"] == "custom":
				colors.update(self.config["layout_owntheme"])

			tmp_dict = {
				"window":				(QPalette.Window,),
				"windowText":			(QPalette.WindowText,),
				"base":					(QPalette.Base,),
				"alternateBase":		(QPalette.AlternateBase,),
				"toolTipBase":			(QPalette.ToolTipBase,),
				"toolTipText":			(QPalette.ToolTipText,),
				"placeholderText":		(QPalette.PlaceholderText,),
				"text":					(QPalette.Text,),
				"button":				(QPalette.Button,),
				"buttonText":			(QPalette.ButtonText,),
				"brightText":			(QPalette.BrightText,),
				"light":				(QPalette.Light,),
				"midlight":				(QPalette.Midlight,),
				"dark":					(QPalette.Dark,),
				"mid":					(QPalette.Mid,),
				"shadow":				(QPalette.Shadow,),
				"highlight":			(QPalette.Highlight,),
				"highlightedText":		(QPalette.HighlightedText,),
				"link":					(QPalette.Link,),
				"linkVisited":			(QPalette.LinkVisited,),

				"disabledButtonText":	(QPalette.Disabled, QPalette.ButtonText),
				"disabledWindowText":	(QPalette.Disabled, QPalette.WindowText),
				"disabledText":			(QPalette.Disabled, QPalette.Text),
				"disabledLight":		(QPalette.Disabled, QPalette.Light),
			}

			mplstyles = colors["mplstyles"]
			palette = QPalette()
			for key, values in tmp_dict.items():
				palette.setColor(*values, colors[key])


		app.setPalette(palette)
		matplotlib.style.use(mplstyles[0])
		self.plotwidget.fig.patch.set_facecolor(mplstyles[1])
		self.plotwidget.fig.patch.set_facecolor(mplstyles[1])
	
	def get_measurement_data(self):
		key_prefixes = ("general", "static", "lockin", "probe", "pump", "refill")
		measurement = {key: value for key, value in mw.config.items() if key.split("_")[0] in key_prefixes}
		return(measurement)
	
	def savemeasurement(self, fname=None):
		if fname is None:
			fname = QFileDialog.getSaveFileName(None, 'Choose file to save measurement to',"","Measurement File (*.meas);;All Files (*)")[0]
		if fname:
			measurement = self.get_measurement_data()
			output_dict = {}
			for key, value in measurement.items():
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

			with open(fname, "w+", encoding="utf-8") as file:
				config_parser.write(file)
			self.notification("Measurement was saved successfully!")

	
	def loadmeasurement(self, fname=None):
		if fname is None:
			fname = QFileDialog.getOpenFileName(None, 'Choose Measurement to load',"","Measurement File (*.meas);;All Files (*)")[0]
		if fname:
			self.loadoptions(fname)
		
	def loadoptions(self, fname=None):
		if not fname:
			self.config.update({key: value[0] for key, value in config_specs.items()})
			fname = customfile(".ini")

		config_parser = configparser.ConfigParser(interpolation=None)
		config_parser.read(fname)

		self.messages = []
		for section in config_parser.sections():
			for key, value in config_parser.items(section):
				fullkey = f"{section.lower()}_{key.lower()}"
				if fullkey in config_specs:
					try:
						class_ = config_specs[fullkey][1]
						if class_ in (dict, list, tuple):
							value = json.loads(value)
						elif class_ == bool:
							value = True if value in ["True", "1"] else False
						value = class_(value)
						self.config[fullkey] = value
					except Exception as E:
						message = f"The value for the option {fullkey} from the option file was not understood."
						self.messages.append(message)
						print(message)
				else:
					self.config[fullkey] = value

	def saveoptions(self, fname=None):
		if fname is None:
			fname = customfile(".ini")
		
		output_dict = {}
		for key, value in self.config.items():
			category, name = key.split("_", 1)
			category = category.capitalize()
			if category not in output_dict:
				output_dict[category] = {}
			if type(value) in (dict, list, tuple):
				value = json.dumps(value)

			output_dict[category][name] = value

		del output_dict["Files"]

		config_parser = configparser.ConfigParser(interpolation=None)
		for section in output_dict:
			config_parser.add_section(section)
			for key in output_dict[section]:
				config_parser.set(section, key, str(output_dict[section][key]))

		with open(fname, "w+", encoding="utf-8") as file:
			config_parser.write(file)
		self.notification("Options were saved successfully!")

	def shortcuts(self):
		shortcuts_dict = {
			"w": lambda: self.plotwidget.set_width("++"),
			"s": lambda: self.plotwidget.set_width("--"),
			"a": lambda: self.plotwidget.set_position("--"),
			"d": lambda: self.plotwidget.set_position("++"),

			"Shift+w": lambda: self.plotwidget.set_width("+"),
			"Shift+s": lambda: self.plotwidget.set_width("-"),
			"Shift+a": lambda: self.plotwidget.set_position("-"),
			"Shift+d": lambda: self.plotwidget.set_position("+"),
			
			"Shift+Q": lambda: self.config.__setitem__("plot_autoscale", True),
			"Ctrl+k": lambda: ConsoleDialog.run(),
		}


		for key, function in shortcuts_dict.items():
			QShortcut(key, self).activated.connect(function)
	
	def get_visible_data(self, type, xrange=None, binning=False, force_all=False, scale=True):
		if type == "exp":
			with locks["exp_df"]:
				dataframe = self.exp_df.copy()
				fd = self.config["files_exp"]
		elif type == "cat":
			with locks["cat_df"]:
				dataframe = self.cat_df.copy()
				fd = self.config["files_cat"]
		elif type == "lin":
			with locks["lin_df"]:
				dataframe = self.lin_df.copy()
				fd = self.config["files_lin"]

		if xrange != None:
			x_start = dataframe["x"].searchsorted(xrange[0], side="left")
			x_stop  = dataframe["x"].searchsorted(xrange[1], side="right")
			dataframe = dataframe.iloc[x_start:x_stop].copy()

		if force_all != True:
			visible_files = {file for file in fd.keys() if not fd[file].get("hidden", False)}
			if len(visible_files) != len(fd):
				# Keep the inplace, as otherwise SettingWithCopyWarning is raised
				dataframe.query("filename in @visible_files", inplace=True)

		if binning:
			bins = mw.config["plot_bins"]
			nobinning = mw.config["plot_skipbinning"]
			binwidth = (xrange[1]-xrange[0]) / bins

			if len(dataframe) > max(bins, nobinning)  and binwidth != 0:
				dataframe = bin_data(dataframe, binwidth, xrange)

		if scale and type in ["exp", "cat"]:
			scalingfactordict = {file: self.config[f"files_{type}"][file].get("scale", 1) for file in fd.keys()}
			dataframe["y"] *= dataframe["filename"].replace(scalingfactordict)

		return(dataframe)

	def return_df(self, type):
		with locks[f"{type}_df"]:
			if type == "exp":
				return(self.exp_df)
			elif type == "cat":
				return(self.cat_df)
			elif type == "lin":
				return(self.lin_df)

	def notification(self, text):
		time_str = time.strftime("%H:%M", time.localtime())
		output = f"{time_str}: {text}"

		if self.config["flag_debug"] == True:
			print(output)
		if self.config["flag_shownotification"]:
			self.signalclass.notification.emit(output)
		if self.config["flag_alwaysshowlog"]:
			mw.logwindow.setVisible(True)
			mw.logwindow.raise_()
		self.signalclass.writelog.emit(output)

	def createmenu(self):
		menus = {label: self.menuBar().addMenu(f"&{label}") for label in ("Files", "View", "Plot", "Actions", "Info")}
		for menu in menus.values():
			menu.setToolTipsVisible(True)

		toggleaction_queue = self.queuewindow.toggleViewAction()
		toggleaction_queue.setShortcut("Shift+4")
		toggleaction_queue.setToolTip("Toggle the visibility of the Queue window")

		toggleaction_log = self.logwindow.toggleViewAction()
		toggleaction_log.setShortcut("Shift+5")
		toggleaction_log.setToolTip("Toggle the visibility of the Log window")
		
		self.pause_after_abort_action = QQ(QAction, parent=self, checkable=True, text="&Pause after aborting", tooltip="If experiment should pause after aborting measurement", change=lambda x:ws.send({"action": "pause_after_abort"}))

		
		actions_to_menus = {
			"Files": (
				QQ(QAction, parent=self, text="&Load Spectrum", change=lambda x: self.load_file("exp", add_files=True), tooltip="Replace Exp file(s)"),
				QQ(QAction, parent=self, text="&Add Spectrum", change=lambda x: self.load_file("exp", add_files=True, keep_old=True), tooltip="Add Exp file(s)"),
				QQ(QAction, parent=self, text="&Load Cat File", change=lambda x: self.load_file("cat", add_files=True), tooltip="Replace Cat file(s)"),
				QQ(QAction, parent=self, text="&Add Cat File", change=lambda x: self.load_file("cat", add_files=True, keep_old=True), tooltip="Add Cat file(s)"),
				QQ(QAction, parent=self, text="&Load Lin File", change=lambda x: self.load_file("lin", add_files=True), tooltip="Replace Lin file(s)"),
				QQ(QAction, parent=self, text="&Add Lin File", change=lambda x: self.load_file("lin", add_files=True, keep_old=True), tooltip="Add Lin file(s)"),
				None,
				QQ(QAction, parent=self, text="&Reread Files", change=self.reread_files, tooltip="Reread all Exp, Cat and Lin files", shortcut="Ctrl+R"),
				None,
				QQ(QAction, parent=self, text="&Edit Files", shortcut="Shift+7", tooltip="See current files and their options", change=lambda x: self.filewindow.show()),
				None,
				QQ(QAction, parent=self, text="&Quit", change=self.close, tooltip="Close the program"),
			),
			"View": (
				QQ(QAction, parent=self, text="&Change Style", tooltip="Change between light, dark and custom theme", change=lambda x: self.change_style()),
				None,
				QQ(QAction, "layout_mpltoolbar", parent=self, text="&MPL Toolbar", shortcut="Shift+1", tooltip="Show or hide toolbar to edit or save the plot canvas", checkable=True),
				QQ(QAction, parent=self, text="&Hover Window", shortcut="Shift+6", tooltip="Show the hover window", change=lambda x: self.hoverwindow.show() and self.hoverwindow.activateWindow()),
				QQ(QAction, parent=self, text="&Config Window", shortcut="Shift+7", tooltip="Show the config window", change=lambda x: self.configwindow.show() and self.configwindow.activateWindow()),
				toggleaction_queue,
				toggleaction_log,
				None,
				QQ(QAction, "flag_alwaysshowlog", parent=self,  text="&Force Show Log", tooltip="Make log window visible if a new message is shown", checkable=True),
			),
			"Plot": (
				QQ(QAction, parent=self, text="&Set Center", tooltip="Set Center", shortcut="Ctrl+G", change=lambda x: self.plotwidget.position_dialog()),
				QQ(QAction, parent=self, text="&Set Width", tooltip="Set specific width", shortcut="Ctrl+W", change=lambda x: self.plotwidget.width_dialog()),
				None,
				QQ(QAction, "flag_automatic_draw", parent=self, text="&Automatic Draw", tooltip="Update canvas automatically when plot is updated, might be switched off if program is unresponsive", checkable = True),
				QQ(QAction, parent=self, text="&Manual Draw", tooltip="Draw canvas manually", change=lambda x: self.plotwidget.manual_draw(), shortcut="Shift+Space"),
			),
			"Actions": (
				QQ(QAction, parent=self, text="&Save Measurement Values", shortcut="Ctrl+S", tooltip="Save current measurement values to file", change=lambda x: self.savemeasurement()),
				QQ(QAction, parent=self, text="&Load Measurement Values", shortcut="Ctrl+O", tooltip="Open measurement and set values accordingly", change=lambda x: self.loadmeasurement()),
				None,
				QQ(QAction, parent=self, text="&Save Queue", tooltip="Save queue to file", change=lambda x: self.queuewindow.savequeue()),
				QQ(QAction, parent=self, text="&Load Queue", tooltip="Load queue from file", change=lambda x: self.queuewindow.loadqueue()),
				None,
				QQ(QAction, parent=self, text="&Save current values as default", shortcut="Ctrl+D", tooltip="Save current configuration as default", change=lambda x: self.saveoptions()),
				None,
				self.pause_after_abort_action,
				QQ(QAction, parent=self, text="&Pop current measurement", tooltip="Put current measurement into queue", change=lambda x:ws.send({"action": "pop_measurement"})),
				None,
				QQ(QAction, parent=self, text="&Next Frequency", shortcut="Ctrl+N", tooltip="Go to next frequency while pausing", change=lambda x:ws.send({"action": "next_frequency"})),
				QQ(QAction, parent=self, text="&Reconnect Experiment", tooltip="Reconnect the websocket to the experiment", change=lambda x: ws.start()),
			),
			"Info": (
				QQ(QAction, parent=self, text="&Send Mail to Author", tooltip="Send a mail to the developer", change=lambda x: send_mail_to_author()),
				QQ(QAction, parent=self, text="&Credits and License", tooltip="See the Credits and License", change=lambda x: self.creditswindow.show()),
			),
		}

		for label, menu in menus.items():
			for widget in actions_to_menus[label]:
				if widget is None:
					menu.addSeparator()
				elif isinstance(widget, QAction):
					menu.addAction(widget)
				else:
					menu.addMenu(widget)

	@askfilesfirst_d
	@threading_d
	@working_d
	def load_file(self, type, keep_old=False, add_files=False, reread=False, skip_update=False, do_QNs=True):
		if reread == True:
			keep_old = False
			fnames = self.config[f"files_{type}"].keys()
		elif add_files != False:
			fnames = add_files
		else:
			fnames = []

		lock = locks[f"{type}_df"]

		if keep_old == False:
			with lock:
				df = self.return_df(type)
				df.drop(df.index, inplace=True)
				if reread == False:
					self.config[f"files_{type}"].clear()

		results = queue.Queue()
		config_updates = queue.Queue()
		errors = queue.Queue()

		if self.config["flag_loadfilesthreaded"]:
			threads = []
			for fname in fnames:
				t = threading.Thread(target=self.load_file_core, args=(fname, type, config_updates, results, errors, do_QNs))
				t.start()
				threads.append(t)

			for thread in threads:
				thread.join()

		else:
			for fname in fnames:
				# Try except block as otherwise files after an exception are not loaded
				try:
					self.load_file_core(fname, type, config_updates, results, errors, do_QNs)
				except Exception as E:
					pass

		with lock:
			for tmp_dict in list(config_updates.queue):
				self.config[f"files_{type}"].update(tmp_dict)

			df = self.return_df(type)
			if len(fnames) != 0:
				df = df[~df.filename.isin(fnames)]
			results.put(df)
			if type == "exp":
				self.exp_df = pd.concat(list(results.queue), ignore_index=True).sort_values("x", kind="merge")
			elif type == "cat":
				self.cat_df = pd.concat(list(results.queue), ignore_index=True).sort_values("x", kind="merge")
			elif type == "lin":
				self.lin_df = pd.concat(list(results.queue), ignore_index=True).sort_values("x", kind="merge")

		df = self.return_df(type)
		if type == "exp":
			self.yrange_exp = np.array((df["y"].min(), df["y"].max()))

		elif type == "cat":
			self.yrange_exp = np.array((df["y"].min(), df["y"].max()))

		elif type == "lin":
			pass

		errors = list(errors.queue)
		self.signalclass.fileschanged.emit()
		if skip_update != True:
			self.plotwidget.set_data()
		if len(fnames):
			error_text = f"<span style='color:#ff0000;'>ERROR</span>: Reading {type.capitalize()} files not successful. " if len(errors) != 0 else ''
			self.notification(f"{error_text}Read {str(len(fnames)-len(errors))+'/' if len(errors) != 0 else ''}{len(fnames)} {type.capitalize()} files successfully.")

	def load_file_core(self, fname, type, config_updates, results, errors, do_QNs):
		try:
			if not os.path.isfile(fname):
				errors.put(fname)
				self.notification(f"<span style='color:#ff0000;'>ERROR</span>: The file {fname} could not be found. Please check the file.")
			if os.path.getsize(fname) == 0:
				return

			options = self.config[f"files_{type}"].get(fname, {})
			extension = os.path.splitext(fname)[1]

			if options.get("color") == None:
				options["color"] = self.config[f"color_{type}"]

			if type == "exp":
				args = (chr(self.config["flag_separator"]), self.config["flag_xcolumn"], self.config["flag_ycolumn"], False)
				data = exp_to_df(fname, *args)
				options["xrange"] = [data["x"].min(), data["x"].max()]
			elif type == "cat":
				formats = self.config["flag_predictionformats"]
				if extension in formats.keys():
					format = formats[extension].copy()
					intens_log = format.get("intensity_log")
					if intens_log:
						del format["intensity_log"]
					data = pd.read_fwf(fname, **format)
					data["filename"] = fname
					if intens_log:
						data["y"] = 10 ** data["y"]
					for column in cat_dtypes.keys():
						if column not in data.columns:
							data[column] = pyckett.SENTINEL
					data = data[cat_dtypes.keys()]
				else:
					data = pyckett.cat_to_df(fname, False)
			elif type == "lin":
				formats = self.config["flag_assignmentformats"]
				if extension in formats.keys():
					format = formats[extension].copy()
					data = pd.read_fwf(fname, dtype=dtypes_dict, **format)
					for column in lin_dtypes.keys():
						if column not in data.columns:
							data[column] = pyckett.SENTINEL
					data = data[lin_dtypes.keys()]
				else:
					data = pyckett.lin_to_df(fname, False)

			config_updates.put({fname: options})
			results.put(data)
		except Exception as E:
			self.notification(f"<span style='color:#ff0000;'>ERROR</span>: There occurred an error when loading the {type.capitalize()} File {fname}. Please check the file.")
			if self.config["flag_debug"]:
				tb.print_exc()
			errors.put(fname)
			raise

	@threading_d
	def reread_files(self, do_QNs=False):
		kwargs = {"reread": True, "skip_update": True, "do_QNs": do_QNs}
		threads = []
		for type in ("exp", "cat", "lin"):
			threads.append(self.load_file(type, **kwargs))

		for thread in threads:
			thread.join()
		self.plotwidget.set_data()

	def update_state(self, state):
		color = {
			"disconnected": "#e61022",
			"pausing": "#c4c712",
			"aborting": "#e61022",
			"running": "#17d40d",
			"waiting": "#4287f5",
		}.get(state, "none")
		
		self.stateindicator.setText("  " + state.capitalize() + "  ")
		self.stateindicator.setStyleSheet(f"background-color: {color}")

	def update_pause_after_abort(self, state):
		self.pause_after_abort_action.setChecked(state)

class PlotWidget(QGroupBox):
	def __init__(self, parent):
		super().__init__(parent)
		
		mw = parent
		self.fig = figure.Figure(dpi=mw.config["plot_dpi"])
		mw.config.register("plot_dpi", lambda: self.fig.set_dpi(mw.config["plot_dpi"]))
		self.ax = self.fig.subplots(1, 1)
		self.ax.patch.set_alpha(0)

		
		self.plotcanvas = FigureCanvas(self.fig)
		self.plotcanvas.setMinimumHeight(200)
		self.plotcanvas.setMinimumWidth(200)
		
		self.cid1 = self.fig.canvas.mpl_connect('button_press_event', self.on_click)
		self.cid2 = self.fig.canvas.mpl_connect("motion_notify_event", self.on_hover)
		
		self.mpltoolbar = NavigationToolbar2QT(self.plotcanvas, self)
		self.mpltoolbar.setVisible(mw.config["layout_mpltoolbar"])
		mw.config.register("layout_mpltoolbar", lambda: self.mpltoolbar.setVisible(mw.config["layout_mpltoolbar"]))
		
		
		toplayout = QHBoxLayout()

		buttonsdict = {
			"in":			lambda x: self.set_width("++"),
			"out":			lambda x: self.set_width("--"),
			"left":			lambda x: self.set_position("-"),
			"right":		lambda x: self.set_position("+"),
			"auto":			lambda x: mw.config.__setitem__("plot_autoscale", True),
		}

		for label, func in buttonsdict.items():
			button = QQ(QPushButton, text=label, change=func, visible=mw.config["flag_showmainplotcontrols"])
			toplayout.addWidget(button)
			mw.config.register("flag_showmainplotcontrols", lambda button=button: button.setVisible(mw.config["flag_showmainplotcontrols"]))
		
		self.toplabel = QQ(QLabel, text="", wordwrap=False)
		self.indicator = QQ(QLabel, text="Ready", textFormat=Qt.RichText)
		self.working = queue.Queue()
		mw.signalclass.setindicator.connect(self.indicator.setText)
		
		toplayout.addWidget(self.toplabel, 1)
		toplayout.addWidget(self.indicator)
		
		layout = QVBoxLayout()
		layout.addLayout(toplayout)
		layout.addWidget(self.plotcanvas, 1)
		layout.addWidget(self.mpltoolbar)
		self.setLayout(layout)

		self.set_data_id = None
		mw.signalclass.updateplot.connect(lambda: self.set_data())
		mw.signalclass.drawplot.connect(lambda: self.plotcanvas.draw())

		
		self.meas_array = None
		self.meas_coll = matplotlib.collections.LineCollection(np.zeros(shape=(0,2,2)), colors=mw.config["color_meas"])
		self.ax.add_collection(self.meas_coll)
		
		self.shared_memory = None
		self.freqrange = (0, 10)
		self.intrange = (0, 1)
		self.plots = {
			"exp": None,
			"cat": None,
			"lin": self.ax.scatter([], [], color=mw.config["color_lin"], marker="*", zorder=100),
			"meas": None,
		}
		
		self.timer = QTimer(app)
		self.timer.timeout.connect(self.set_meas_data)
		self.timer.start(mw.config["flag_updateplot"])
		
		mw.config.register("flag_updateplot", lambda: self.timer.start(max(100, mw.config["flag_updateplot"])) if mw.config["flag_updateplot"] > 0 else self.timer.stop())

	def position_dialog(self):
		resp, rc = QInputDialog.getText(self, 'Set center position', 'Frequency:')
		if not rc:
			return

		try:
			self.set_position(float(resp))
		except ValueError:
			mw.notification("<span style='color:#eda711;'>WARNING</span>: The entered value could not be interpreted as a number.")
			return

	def width_dialog(self):
		resp, rc = QInputDialog.getText(self, 'Set width', 'Width:')
		if not rc:
			return
		
		try:
			self.set_width(float(resp))
		except ValueError:
			mw.notification("<span style='color:#eda711;'>WARNING</span>: The entered value could not be interpreted as a number.")
			return

	@synchronized_d(locks["meas"])
	def connect_shared_memory(self, name, size, shape):
		if self.shared_memory:
			self.shared_memory.close()
			self.meas_array = None
		
		self.shared_memory = shared_memory.SharedMemory(name=name, size=size)
		self.meas_array = np.ndarray(shape, dtype=np.float64, buffer=self.shared_memory.buf)
	
	@synchronized_d(locks["meas"])
	def get_meas_data(self, filtered=True):
		if self.meas_array is not None:
			if filtered:
				index_ = np.isnan(self.meas_array[:, -1]).argmax()
				mw.signalclass.progressbar.emit(int(index_ / self.meas_array.shape[0] * 100))
				return(self.meas_array[:index_])
			else:
				return(self.meas_array)
		else:
			return np.ndarray((0,3), dtype=np.float64)

	def set_position(self, value):
		mw.config["plot_autoscale"] = False
		position = np.mean(self.freqrange)
		width = self.freqrange[1] - self.freqrange[0]
		
		if value == "+":
			position += width/4
		elif value == "-":
			position -= width/4
		elif value == "++":
			position += width/2
		elif value == "--":
			position -= width/2
		else:
			position = value
		
		self.freqrange = (position-width/2, position+width/2)
		self.set_data()
		
	def set_width(self, value, absolute=True):
		mw.config["plot_autoscale"] = False
		position = np.mean(self.freqrange)
		width = self.freqrange[1] - self.freqrange[0]
		
		if value == "+":
			width *= 3/4
		elif value == "-":
			width /= 3/4
		elif value == "++":
			width *= 1/2
		elif value == "--":
			width /= 1/2
		elif absolute:
			width = value
		else:
			width *= value
		
		self.freqrange = (position-width/2, position+width/2)
		self.set_data()

	def set_data(self):
		thread = threading.Thread(target=self.set_data_core)
		with locks["currThread"]:
			thread.start()
			self.set_data_id = thread.ident
		return(thread)

	@working_d
	@synchronized_d(locks["axs"])
	def set_data_core(self):
		with locks["currThread"]:
			ownid = threading.current_thread().ident

		try:
			if not mw.config["flag_automatic_draw"]:
				return

			breakpoint(ownid, self.set_data_id)

			ax = self.ax
			autoscale = mw.config["plot_autoscale"]
			self.set_meas_data(standalone=False)
			xmin, xmax = self.freqrange
			datatypes = ("exp", "cat", "lin")
			dataframes = {key: mw.get_visible_data(key, xrange=(xmin, xmax), binning=True, scale=True) for key in datatypes}
			files_dicts = {key: mw.config[f"files_{key}"] for key in datatypes}

			breakpoint(ownid, self.set_data_id)

			# Exp Data
			dataframe, files = dataframes["exp"], files_dicts["exp"]
			xs, ys = dataframe["x"], dataframe["y"]
			if mw.config["plot_expasstickspectrum"]:
				segs = np.array(((xs, xs), (ys*0, ys))).T
				colors = create_colors(dataframe, files)
			else:
				filenames = dataframe["filename"].to_numpy()
				unique_filenames = np.unique(filenames)

				segs = []
				colors = []
				for unique_filename in unique_filenames:
					mask = (filenames == unique_filename)
					tmp_xs, tmp_ys = xs[mask], ys[mask]

					segs.append(np.array(((tmp_xs[:-1], tmp_xs[1:]), (tmp_ys[:-1], tmp_ys[1:]))).T)
					colors.extend([files[unique_filename]["color"]]*sum(mask))

				if segs:
					segs = np.concatenate(segs)
			coll = matplotlib.collections.LineCollection(segs, colors=colors)
			if self.plots["exp"]:
				self.plots["exp"].remove()
			self.plots["exp"] = ax.add_collection(coll)
			
			# Cat Data
			dataframe, files = dataframes["cat"], files_dicts["cat"]
			xs, ys = dataframe["x"], dataframe["y"]
			
			if autoscale:
				if len(dataframe):
					yrange_cat = [ys.min(), ys.max()]
				else:
					yrange_cat = [-1, 1]
				ys = ys*self.intrange[1]/yrange_cat[1]
			else:
				ys = ys*mw.config["plot_expcat_factor"]*10**mw.config["plot_expcat_exponent"]
			segs = np.array(((xs, xs), (ys*0, ys))).T

			colors = create_colors(dataframe, files)
			coll = matplotlib.collections.LineCollection(segs, colors=colors)
			if self.plots["cat"]:
				self.plots["cat"].remove()
			self.plots["cat"] = ax.add_collection(coll)
			
			# Lin Data
			dataframe, files = dataframes["lin"], files_dicts["lin"]
			xs, ys = dataframe["x"], dataframe["x"]*0
			
			tuples = list(zip(xs, ys))
			tuples = tuples if len(tuples)!=0 else [[None,None]]
			colors = create_colors(dataframe, files)

			self.plots["lin"].set_offsets(tuples)
			self.plots["lin"].set_color(colors)

			breakpoint(ownid, self.set_data_id)
			
			mw.signalclass.drawplot.emit()

		except CustomError as E:
			pass

	@synchronized_d(locks["axs"])
	def set_meas_data(self, standalone=True):
		meas_data = self.get_meas_data()
		if mw.tabwidget.currentIndex():
			return
		ax = self.ax
		autoscale = mw.config["plot_autoscale"]
		xs, ys = meas_data[:, 0], meas_data[:, 2]
		
		if autoscale:
			self.freqrange = (xs.min(), xs.max()) if len(xs) else (0, 10)
			self.intrange = (ys.min(), ys.max()) if len(ys) else (0, 1)
		
		xmin, xmax = self.freqrange
		ymin, ymax = self.intrange

		segs = np.array(((xs[:-1], xs[1:]), (ys[:-1], ys[1:]))).T
		self.meas_coll.set(segments=segs, color=mw.config["color_meas"])

		margin = mw.config["plot_ymargin"]
		yrange = [ymin-margin*(ymax-ymin), ymax+margin*(ymax-ymin)]
		if np.isnan(yrange[0]) or np.isnan(yrange[1]) or yrange[0] == yrange[1]:
			yrange = self.intrange = [-1,+1]
		
		ax.set_ylim(*yrange)
		
		if np.isnan(xmin) or np.isnan(xmax) or xmin == xmax:
			xmin, xmax = self.freqrange =  0, 10
		ax.set_xlim(xmin, xmax)
		
		ticks = np.linspace(xmin, xmax, mw.config["plot_ticks"])
		if mw.config["plot_scientificticks"]:
			ticklabels = [f"{x:.2e}".replace("e+00", "").rstrip("0").rstrip(".") for x in ticks]
		else:
			ticklabels = symmetric_ticklabels(ticks)
		ax.set_xticks(ticks)
		ax.set_xticklabels(ticklabels)
		
		if standalone:
			mw.signalclass.drawplot.emit()
		
	def on_click(self, event):
		pass
	
	def on_hover(self, event):
		x = event.xdata
		y = event.ydata

		if not all([x, y, event.inaxes]):
			text_top = ""
			text_annotation = ""
		else:
			if mw.config["flag_showmainplotposition"]:
				text_top = f"({x=:.4f}, {y=:.4f})"
			else:
				text_top = ""

			cutoff = mw.config["plot_hover_cutoff"]
			xrange = (x-cutoff, x+cutoff)
			cat_df = mw.get_visible_data("cat", xrange=xrange)
			lin_df = mw.get_visible_data("lin", xrange=xrange)

			dataframes = {"cat": cat_df, "lin": lin_df}
			transitions = {}
			noq = mw.config["flag_qns"]

			for type, df in dataframes.items():
				if len(df):
					df["dist"] = abs(df["x"] - x)
					smallest_distance = df["dist"].min()
					df = df.query("dist == @smallest_distance")

					tmp = []
					for i, row in df.iterrows():
						qnus = [row[f"qnu{i+1}"] for i in range(noq)]
						qnls = [row[f"qnl{i+1}"] for i in range(noq)]
						tmp.append(f"{', '.join([str(qn) for qn in qnus if qn != pyckett.SENTINEL])} ← {', '.join([str(qn) for qn in qnls if qn != pyckett.SENTINEL])}")

					transitions[type] = tmp

			text_annotation = []
			if "cat" in transitions:
				text_annotation.append("Cat:\n" + "\n".join(transitions["cat"]))
			if "lin" in transitions:
				text_annotation.append("Lin:\n" + "\n".join(transitions["lin"]))

			if text_annotation:
				text_annotation = "\n\n".join(text_annotation)
			else:
				text_annotation = ""

		mw.signalclass.writehover.emit(text_annotation)
		self.toplabel.setText(text_top)


class Websocket():
	def __init__(self):
		self.start()
		
	def start(self):
		try:
			self.close()
		except Exception as E:
			pass
		self.websocket = websocket.WebSocketApp("ws://localhost:8112", on_message=self.on_message, on_error=self.on_error, on_close=self.on_close, on_open=self.on_open)
		self.thread = threading.Thread(target=self.websocket.run_forever)
		self.thread.start()
		mw.ws = self.websocket
	
	def close(self):
		self.websocket.close()
		
	def on_message(self, ws, message):
		message = json.loads(message)
		mw.signalclass.websocketaction.emit(message)

	def on_error(self, ws, error):
		message = {"action": "connection_error", "error": error}
		mw.signalclass.websocketaction.emit(message)

	def on_close(self, ws, close_status_code, close_msg):
		message = {"action": "closed"}
		mw.signalclass.websocketaction.emit(message)

	def on_open(self, ws):
		message = {"action": "opened"}
		mw.signalclass.websocketaction.emit(message)

	def send(self, message):
		self.websocket.send(json.dumps(message))


class CustomError(Exception):
	pass

class Config(dict):
	def __init__(self, signal):
		super().__init__()
		self.signal = signal
		self.signal.connect(self.callback)
		self.callbacks = pd.DataFrame(columns=["id", "key", "widget", "function"], dtype="object").astype({"id": np.uint})


	def __setitem__(self, key, value, widget=None):
		super().__setitem__(key, value)
		self.signal.emit((key, value, widget))

	def callback(self, args):
		key, value, widget = args
		if widget:
			callbacks_widget = self.callbacks.query(f"key == @key and widget != @widget")
		else:
			callbacks_widget = self.callbacks.query(f"key == @key")
		for i, row in callbacks_widget.iterrows():
			row["function"]()

	def register(self, keys, function):
		if not isinstance(keys, (tuple, list)):
			keys = [keys]
		for key in keys:
			id = 0
			df = self.callbacks
			df.loc[len(df), ["id", "key", "function"]] = id, key, function

	def register_widget(self, key, widget, function):
		ids = set(self.callbacks["id"])
		id = 1
		while id in ids:
			id += 1
		df = self.callbacks
		df.loc[len(df), ["id", "key", "function", "widget"]] = id, key, function, widget
		widget.destroyed.connect(lambda x, id=id: self.unregister_widget(id))

	def unregister_widget(self, id):
		self.callbacks.drop(self.callbacks[self.callbacks["id"] == id].index, inplace=True)


class SignalClass(QObject):
	fileschanged      = pyqtSignal()
	assignment        = pyqtSignal()
	updateplot        = pyqtSignal()
	drawplot          = pyqtSignal()
	createdplots      = pyqtSignal()
	blwfit            = pyqtSignal()
	peakfinderstart   = pyqtSignal()
	peakfinderend     = pyqtSignal()
	overlapend        = pyqtSignal()
	overlapindicator  = pyqtSignal(str)
	fitindicator      = pyqtSignal(str)
	setindicator      = pyqtSignal(str)
	writelog          = pyqtSignal(str)
	writehover        = pyqtSignal(str)
	notification      = pyqtSignal(str)
	websocketaction   = pyqtSignal(dict)
	updateconfig      = pyqtSignal(tuple)
	updatemeasurement = pyqtSignal(tuple)
	progressbar       = pyqtSignal(int)
	def __init__(self):
		super().__init__()

class Color(str):
	def __new__(cls, color):
		cls.validate_color(cls, color)
		return super().__new__(cls, color)

	def __assign__(self, color):
		self.validate_color(color)
		return super().__new__(color)

	def validate_color(self, color):
		match = re.search(r'^#(?:[0-9a-fA-F]{3}?){1,2}$|^#(?:[0-9a-fA-F]{8}?)$', color)
		if match:
			if len(color) == 9 and color[-2:] == "ff":
				color = color[:-2]
			return(color)
		else:
			raise CustomError(f"Invalid Color: '{color}' is not a valid color.")

class NotificationsBox(QWidget):
	def __init__(self):
		super().__init__()
		self.bg_color = QColor("#a5aab3")
		self.messages = []
		self.setWindowFlags(
			Qt.Window | Qt.Tool | Qt.FramelessWindowHint |
			Qt.WindowStaysOnTopHint | Qt.X11BypassWindowManagerHint)

		self.setAttribute(Qt.WA_NoSystemBackground, True)
		self.setAttribute(Qt.WA_TranslucentBackground, True)

		self.setMinimumHeight(80)
		self.setMinimumWidth(300)
		self.setMaximumWidth(300)

		self.layout = QVBoxLayout()
		self.setLayout(self.layout)

		self.setStyleSheet("""
			color: white;
			background-color: #bf29292a;
		""")

		self._desktop = QApplication.instance().desktop()
		startPos = QPoint(self._desktop.screenGeometry().width() - self.width() - 10, 10)
		self.move(startPos)

	def paintEvent(self, event=None):
		painter = QPainter(self)

		painter.setOpacity(0.5)
		painter.setPen(QPen(self.bg_color))
		painter.setBrush(self.bg_color)
		painter.drawRect(self.rect())

	def add_message(self, text):
		label = QLabel(text)
		label.setWordWrap(True)
		label.setStyleSheet("""
			padding: 5px;
		""")

		self.layout.addWidget(label)
		self.messages.append(label)
		self.timer = QTimer(self)
		self.timer.setSingleShot(True)
		self.timer.timeout.connect(self.unshow_message)
		self.timer.start(mw.config["flag_notificationtime"])

		self.show()

		self.timer2 = QTimer(self)
		self.timer2.setSingleShot(True)
		self.timer2.timeout.connect(self.adjustSize)
		self.timer2.start(0)


	def unshow_message(self):
		label = self.messages.pop()
		label.hide()
		label.deleteLater()
		if not self.messages:
			self.hide()
		self.adjustSize()

class QComboBox(QComboBox):
	def __init__(self, *args, **kwargs):
		result = super().__init__(*args, **kwargs)
		self.currentTextChanged.connect(self.check_if_valid_option)
		return(result)

	def setCurrentText(self, text):
		self.check_if_valid_option(text)
		return(super().setCurrentText(text))

	def check_if_valid_option(self, text):
		items = [self.itemText(i) for i in range(self.count())]
		if text not in items:
			self.setStyleSheet("QComboBox {background-color: red;}")
		else:
			self.setStyleSheet("")

class QBoolComboBox(QComboBox):
	def __init__(self, *args, **kwargs):
		self.false_text = "No"
		self.true_text = "Yes"
		
		tmp = super().__init__(*args, **kwargs)
		self.addItem(self.true_text)
		self.addItem(self.false_text)
		return(tmp)
		
	def currentText(self):
		return super().currentText() == self.true_text

	def setCurrentText(self, value):
		if value:
			super().setCurrentText(self.true_text)
		else:
			super().setCurrentText(self.false_text)

class QSpinBox(QSpinBox):
	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)
		# AdaptiveDecimalStepType is not implemented in earlier versions of PyQt5
		try:
			self.setStepType(QAbstractSpinBox.AdaptiveDecimalStepType)
		except:
			pass

	def setSingleStep(self, value):
		self.setStepType(QAbstractSpinBox.DefaultStepType)
		super().setSingleStep(value)

	def setValue(self, value):
		if value < -2147483647 or value > 2147483647:
			value = 0
		return super().setValue(value)

	def setRange(self, min, max):
		min = min if not min is None else -2147483647
		max = max if not max is None else +2147483647
		return super().setRange(min, max)

class QDoubleSpinBox(QDoubleSpinBox):
	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)
		self.setDecimals(20)
		# AdaptiveDecimalStepType is not implemented in earlier versions of PyQt5
		try:
			self.setStepType(QAbstractSpinBox.AdaptiveDecimalStepType)
		except:
			pass

	def setSingleStep(self, value):
		self.setStepType(QAbstractSpinBox.DefaultStepType)
		super().setSingleStep(value)

	def textFromValue(self, value):
		# if value and abs(np.log10(abs(value))) > 5:
			# return(f"{value:.2e}")
		# else:
			# return(f"{value:.10f}".rstrip("0").rstrip("."))
		return(f"{value:.10f}".rstrip("0").rstrip("."))


	def valueFromText(self, text):
		return(np.float64(text))

	def setRange(self, min, max):
		min = min if not min is None else -np.inf
		max = max if not max is None else +np.inf
		return super().setRange(min, max)

	def validate(self, text, position):
		try:
			np.float64(text)
			return(2, text, position)
		except ValueError:
			if text.strip() in ["+", "-", ""]:
				return(1, text, position)
			elif re.match(r"^[+-]?\d+\.?\d*[Ee][+-]?\d?$", text):
				return(1, text, position)
			else:
				return(0, text, position)

	def fixup(self, text):
		tmp = re.search(r"[+-]?\d+\.?\d*", text)
		if tmp:
			return(tmp[0])
		else:
			return(str(0))

class QSweep(QWidget):

	changed = pyqtSignal()

	def __init__(self, *args, **kwargs):
		super().__init__()
		
		self._state = {
			"pointsmode": "points",
			"rangemode": "center",
			"updating": False,
		}
				
		layout = QGridLayout()
		layout.setContentsMargins(0, 0, 0, 0)
		layout.setColumnStretch(2, 2)
		self.setLayout(layout)
		
		
		self.labels = {}
		self.widgets = {
			"Mode": QQ(QComboBox, options=("fixed", "sweep"), change=self.update_state),
			"Iterations": QQ(QSpinBox, range=(1, None), change=self.update_state),
			"Direction": QQ(QComboBox, options=("forth", "forthback", "back", "backforth", "fromcenter", "random"), change=self.update_state),
			"Start": QQ(QDoubleSpinBox, range=(0, None), change=self.update_state),
			"Stop": QQ(QDoubleSpinBox, range=(0, None), change=self.update_state),
			"Center": QQ(QDoubleSpinBox, range=(0, None), change=self.update_state),
			"Span": QQ(QDoubleSpinBox, range=(0, None), change=self.update_state),
			"Points": QQ(QDoubleSpinBox, range=(0, None), change=self.update_state),
			"Stepsize": QQ(QDoubleSpinBox, range=(0, None), change=self.update_state),
		}
		
		self.toggles = {
			"Start": QQ(QToolButton, text="🗘", change=self.togglerangemode),
			"Stop": QQ(QToolButton, text="🗘", change=self.togglerangemode),
			"Center": QQ(QToolButton, text="🗘", change=self.togglerangemode),
			"Span": QQ(QToolButton, text="🗘", change=self.togglerangemode),
			"Points": QQ(QToolButton, text="🗘", change=self.togglepointsmode),
			"Stepsize": QQ(QToolButton, text="🗘", change=self.togglepointsmode),
		}
		
		for rowindex, (label, widget) in enumerate(self.widgets.items()):
			labelw = QQ(QLabel, text=label)
			self.labels[label] = labelw
			layout.addWidget(labelw, rowindex, 0)
			
			toggle = self.toggles.get(label)
			if toggle:
				layout.addWidget(toggle, rowindex, 1)
				layout.addWidget(widget, rowindex, 2)
			else:
				layout.addWidget(widget, rowindex, 1, 1, 2)
	
	def togglerangemode(self, *args, **kwargs):
		if self._state["rangemode"] == "center":
			center = self.widgets["Center"].value()
			span = self.widgets["Span"].value()
			
			self.widgets["Start"].setValue(center - span/2)
			self.widgets["Stop"].setValue(center + span/2)
			
			self._state["rangemode"] = "range"
		else:
			start = self.widgets["Start"].value()
			stop = self.widgets["Stop"].value()
			
			self.widgets["Center"].setValue((start + stop)/2)
			self.widgets["Span"].setValue(stop - start)
			
			self._state["rangemode"] = "center"
		
		self.update_state()
	
	def togglepointsmode(self, *args, **kwargs):
		if self._state["rangemode"] == "center":
			width = self.widgets["Span"].value()
		else:
			width = self.widgets["Stop"].value() - self.widgets["Start"].value()
		
		if self._state["pointsmode"] == "points":
			points = self.widgets["Points"].value() or 100
			self.widgets["Stepsize"].setValue(width / points * 1000)
			self._state["pointsmode"] = "steps"
		else:
			stepsize = self.widgets["Stepsize"].value() or 25
			self.widgets["Points"].setValue(width / stepsize * 1000)
			self._state["pointsmode"] = "points"
		
		self.update_state()
	
	def setState(self, state):
		self._state["updating"] = True
		self.widgets["Mode"].setCurrentText(state["mode"])
		
		if state["mode"] == "fixed":
			self.widgets["Center"].setValue(state["center"])
		else:
			self.widgets["Direction"].setCurrentText(state.get("direction", "forthback"))
			self.widgets["Iterations"].setValue(state.get("iterations", 1))
			
			if "center" in state and "span" in state:
				self.widgets["Center"].setValue(state["center"])
				self.widgets["Span"].setValue(state["span"])
				self._state["rangemode"] = "center"
			else:
				self.widgets["Start"].setValue(state["start"])
				self.widgets["Stop"].setValue(state["stop"])
				self._state["rangemode"] = "range"
			
			if "points" in state:
				self.widgets["Points"].setValue(state["points"])
				self._state["pointsmode"] = "points"
			else:
				self.widgets["Stepsize"].setValue(state["stepsize"])
				self._state["pointsmode"] = "steps"
		
		self._state["updating"] = False
		self.update_state()
		
	def update_state(self):
		if self._state["updating"]:
			return
		
		keys = set(self.widgets.keys())
		state = self.getState()
		
		if state["mode"] == "fixed":
			hidden_keys = keys - {"Mode", "Center"}
		
		else:
			hidden_keys = []
			if self._state["rangemode"] == "center":
				hidden_keys.extend(("Start", "Stop"))
			else:
				hidden_keys.extend(("Center", "Span"))
			
			if self._state["pointsmode"] == "points":
				hidden_keys.append("Stepsize")
			else:
				hidden_keys.append("Points")
				
		
		for key in keys:
			hidden = True if key in hidden_keys else False
			self.labels[key].setHidden(hidden)
			self.widgets[key].setHidden(hidden)
			if self.toggles.get(key):
				self.toggles[key].setHidden(hidden)
		
		if state["mode"] == "fixed":
			self.toggles["Center"].setHidden(True)
		
		self.changed.emit()
		
	def getState(self):
		state = {"mode": self.widgets["Mode"].currentText(), }
		
		if state["mode"] == "fixed":
			state["center"] = self.widgets["Center"].value()
		else:
			state["direction"] = self.widgets["Direction"].currentText()
			state["iterations"] = self.widgets["Iterations"].value()
			if self._state["rangemode"] == "center":
				state.update({
					"center": self.widgets["Center"].value(),
					"span": self.widgets["Span"].value(),
				})
			else:
				state.update({
					"start": self.widgets["Start"].value(),
					"stop": self.widgets["Stop"].value(),
				})
			
			if self._state["pointsmode"] == "points":
				state["points"] = self.widgets["Points"].value()
			else:
				state["stepsize"] = self.widgets["Stepsize"].value()
		
		return(state)

class EQWidget(QWidget):
	def __init__(self, id, parent=None):
		self.id = id
		super().__init__(parent)

		geometry = mw.config.get(f"windowgeometry_{self.id}")
		if geometry:
			if isinstance(geometry, str):
				geometry = json.loads(geometry)
			self.setGeometry(*geometry)

		QShortcut("Esc", self).activated.connect(self.close)

	@synchronized_d(locks["windows"])
	def closeEvent(self, *args, **kwargs):
		mw.config[f"windowgeometry_{self.id}"] = self.geometry().getRect()
		return super().closeEvent(*args, **kwargs)

	def resizeEvent(self, event):
		mw.config[f"windowgeometry_{self.id}"] = self.geometry().getRect()

	def moveEvent(self, event):
		mw.config[f"windowgeometry_{self.id}"] = self.geometry().getRect()

class MeasWidget(QGroupBox):
	def __init__(self, parent):
		super().__init__(parent)
		mw.measurementlayout.addWidget(self, *self.gridpos)
		
		self.setTitle(self.title)
		layout = QGridLayout()
		self.setLayout(layout)

		for i, (label, widget) in enumerate(self.widgets.items()):
			if isinstance(widget, QSweep):
				layout.addWidget(widget, i, 0, 1, 2)
			else:
				layout.addWidget(QQ(QLabel, text=label), i, 0)
				layout.addWidget(widget, i, 1)
		
		layout.setColumnStretch(1, 2)
		layout.setRowStretch(len(self.widgets), 2)

class EQDockWidget(QDockWidget):
	def __init__(self, parent):
		super().__init__(parent)
		self.setObjectName(self.__class__.__name__)

		parent.addDockWidget(self.dockpos, self)
		QShortcut("Esc", self).activated.connect(self.close)

class QueueWindow(EQDockWidget):
	def __init__(self, parent):
		self.dockpos = 8
		
		super().__init__(parent)
		self.setWindowTitle("Queue")
		
		mainwidget = QGroupBox()
		self.setWidget(mainwidget)
		
		self.layout = QVBoxLayout()
		mainwidget.setLayout(self.layout)
		self.buttonslayout = QHBoxLayout()
		self.layout.addLayout(self.buttonslayout)
		
		keys = ("Add last", "Add first", "Run now", "Add list", "Add batch", "Del all")
		self.buttons = {key: QQ(QToolButton, text=key, change=lambda x, key=key: self.command(key)) for key in keys}
		for button in self.buttons.values():
			self.buttonslayout.addWidget(button)
		self.buttonslayout.addStretch(2)
		
		self.listwidget = QListWidget()
		self.listwidgetmodel = self.listwidget.model()
		self.listwidgetmodel.rowsMoved.connect(self.rowsmoved)
		
		self.layout.addWidget(self.listwidget)
		self.listwidget.setDragDropMode(QAbstractItemView.InternalMove)
		self.listwidget.setSelectionMode(QAbstractItemView.ExtendedSelection)
		self.listwidget.setFont(QFont("Courier"))
	
		self.queue = []
	
		QShortcut(QKeySequence(Qt.Key_Delete), self).activated.connect(self.deleterow);

	def deleterow(self, *args, **kwargs):
		indices = [x.row() for x in self.listwidget.selectedIndexes()]
		ws.send({"action": "del_measurement", "indices": indices})

	
	def rowsmoved(self, *args, **kwargs):
		_, oldindex, _, _, newindex = args
		if newindex > oldindex:
			newindex -= 1
		ws.send({"action": "reorder_measurement", "oldindex": oldindex, "newindex": newindex})
	
	def command(self, key):
		measurement = mw.get_measurement_data()

		if key == "Add last":
			ws.send({"action": "add_measurement_last", "measurement": measurement})
		elif key == "Add first":
			ws.send({"action": "add_measurement_first", "measurement": measurement})
		elif key == "Run now":
			ws.send({"action": "add_measurement_now", "measurement": measurement})
		elif key == "Del all":
			reply = QMessageBox.question(self, 'Delete all', 'Are you sure you want to delete all pending measurements?', QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
			if reply == QMessageBox.Yes:
				ws.send({"action": "del_measurements"})
		elif key == "Add list":
			fname = QFileDialog.getOpenFileName(None, 'Choose List to load',"")[0]
			if not fname:
				return
			
			with open(fname) as file:
				list_ = file.read()
			
			list_ = np.genfromtxt(list_.split("\n"), delimiter="\t", names=True, deletechars='')
			headers = list_.dtype.names
			measurements = []
			
			for i, row in enumerate(list_):
				update_dict = {key: value for key, value in zip(headers, row)}
				tmp_measurement = copy.deepcopy(measurement)
				
				for header, value in update_dict.items():
					if header in tmp_measurement.keys():
						tmp_measurement[header] = value
					elif header.startswith("probe.") or header.startswith("pump."):
						source, suffix = header.split(".", 1)
						tmp_measurement[f"{source}_frequency"][suffix] = value
					else:
						raise CustomError(f"Did not understand the header {header} in you list.")
				
				measurements.append(tmp_measurement)
			ws.send({"action": "add_measurements", "measurements": measurements})
		
		elif key == "Add batch":
			dialog = BatchDialog()
			dialog.exec_()

			if dialog.result() == 1:
				result = dialog.save()
				measurements = []
				
				for i_pump in range(result["pump"]):
					for i_probe in range(result["probe"]):
						tmp_measurement = copy.deepcopy(measurement)
						
						for source in ("probe", "pump"):
							i = i_pump if source == "pump" else i_probe
							tmp_dict = tmp_measurement[f"{source}_frequency"]
							
							if tmp_dict.get("mode") != "fixed":
								if "center" in tmp_dict and "span" in tmp_dict:
									tmp_dict["center"] += tmp_dict["span"] * i
								else:
									start, stop = tmp_dict["start"], tmp_dict["stop"]
									span  = stop - start
									tmp_dict["start"] += span * i
									tmp_dict["stop"] += span * i
						
						measurements.append(tmp_measurement)
				
				ws.send({"action": "add_measurements", "measurements": measurements})
		
		# @Luis: Command to set autophase
		# @Luis: Maybe also option to measure x or magnitude
	
	def update_queue(self, queue):
		self.queue = queue
		self.listwidget.clear()
		spacer = " | "
		for measurement in queue:
			listwidgetitem = QListWidgetItem()
			
			measurement_string = []
			
			mode = measurement["general_mode"].upper()
			measurement_string.append(f"{mode:7}")
			measurement_string.append(spacer)
			
			frequencies = {}
			for type in ("probe", "pump"):
				tmp_dict = measurement.get(f"{type}_frequency")
				if not tmp_dict:
					continue
				
				if tmp_dict["mode"] == "fixed":
					frequencies[type] = (tmp_dict["center"], 0)
				elif "center" in tmp_dict and "span" in tmp_dict:
					frequencies[type] = (tmp_dict["center"], tmp_dict["span"])
				else:
					start, stop = tmp_dict["start"], tmp_dict["stop"]
					frequencies[type] = ((start + stop) / 2, stop - start)
			
			probe, probe_width = frequencies["probe"]
			probe_string = f"Probe: {probe:10.2f}"
			if probe_width:
				probe_string += f"/{probe_width:7.2f}"
			
			measurement_string.append(f"{probe_string:25}")
			
			if measurement["general_mode"] != devices.modes[0]:
				measurement_string.append(spacer)
				pump, pump_width = frequencies["pump"]
				pump_string = f"Pump: {pump:10.2f}"
			
				if pump_width:
					pump_string += f"/{pump_width:7.2f}"
				
				measurement_string.append(f"{pump_string:24}")

			tmp = "".join(measurement_string)
			listwidgetitem.setText(tmp)
			listwidgetitem.setToolTip("\n".join([f"{key}: {value}" for key, value in measurement.items()]))
			self.listwidget.addItem(listwidgetitem)

	def savequeue(self):
		fname = QFileDialog.getSaveFileName(None, 'Choose file to save queue to',"","Queue File (*.queue);;All Files (*)")[0]
		if not fname:
			return
		
		with open(fname, "w+") as file:
			file.write(json.dumps(self.queue))
	
	def loadqueue(self):
		fname = QFileDialog.getOpenFileName(None, 'Choose queue to load',"","Queue File (*.queue);;All Files (*)")[0]
		if not fname:
			return
		
		with open(fname, "r") as file:
			queue = json.loads(file.read())
		
		for measurement in queue:
			ws.send({"action": "add_measurement_last", "measurement": measurement})

class BatchDialog(QDialog):
	def __init__(self):
		super().__init__()
		QShortcut("Esc", self).activated.connect(lambda: self.predone(0))

		self.setWindowTitle(f"Batch Dialog")
		self.resize(mw.config["batchdialog_width"], mw.config["batchdialog_height"])

		measurement = mw.get_measurement_data()
		self.widgets = {}
		
		layout = QGridLayout()
		self.setLayout(layout)
		current_row = 0
		columns = 2
		
		for source in ("probe", "pump"):
			Source = source.capitalize()
			if current_row:
				layout.setRowStretch(current_row, 2)
				current_row += 1
			layout.addWidget(QQ(QLabel, text=Source), current_row, 0, 1, columns)
			current_row += 1
			self.widgets[source] = {}
			tmp_dict = measurement[f"{source}_frequency"]
			if tmp_dict["mode"] != "sweep":
				layout.addWidget(QQ(QLabel, text=f"Please choose sweep as mode in the {Source} panel to activate this section.", wordwrap=True), current_row, 0, 1, columns)
				current_row += 1
			else:
				if "span" in tmp_dict and "center" in tmp_dict:
					center, span = tmp_dict["center"], tmp_dict["span"]
					
					self.widgets[source]["center"] = tmp = QQ(QDoubleSpinBox, range=(0, None), value=center, change=self.update)
					layout.addWidget(QQ(QLabel, text="Center first Measurement: "), current_row, 0)
					layout.addWidget(tmp, current_row, 1)
					current_row += 1
					
					self.widgets[source]["span"] = tmp = QQ(QDoubleSpinBox, range=(0, None), value=span, change=self.update)
					layout.addWidget(QQ(QLabel, text="Span first Measurement: "), current_row, 0)
					layout.addWidget(tmp, current_row, 1)
					current_row += 1
				else:
					start, stop = tmp_dict["start"], tmp_dict["stop"]
					
					self.widgets[source]["start"] = tmp = QQ(QDoubleSpinBox, range=(0, None), value=start, change=self.update)
					layout.addWidget(QQ(QLabel, text="Start first Measurement: "), current_row, 0)
					layout.addWidget(tmp, current_row, 1)
					current_row += 1
					
					self.widgets[source]["stop"] = tmp = QQ(QDoubleSpinBox, range=(0, None), value=stop, change=self.update)
					layout.addWidget(QQ(QLabel, text="Stop first Measurement: "), current_row, 0)
					layout.addWidget(tmp, current_row, 1)
					current_row += 1

				
				self.widgets[source]["measurements"] = tmp = QQ(QSpinBox, range=(1, None), change=self.update)
				layout.addWidget(QQ(QLabel, text="Number of Measurements: "), current_row, 0)
				layout.addWidget(tmp, current_row, 1)
				current_row += 1
				
				self.widgets[source]["starttotal"] = tmp = QQ(QDoubleSpinBox, range=(0, None), enabled=False)
				layout.addWidget(QQ(QLabel, text="Start Frequency All: "), current_row, 0)
				layout.addWidget(tmp, current_row, 1)
				current_row += 1
				
				self.widgets[source]["stoptotal"] = tmp = QQ(QDoubleSpinBox, range=(0, None), enabled=False)
				layout.addWidget(QQ(QLabel, text="Stop Frequency All: "), current_row, 0)
				layout.addWidget(tmp, current_row, 1)
				current_row += 1
		
		layout.setRowStretch(current_row, 2)
		current_row += 1
		
		buttons = QDialogButtonBox.Ok | QDialogButtonBox.Cancel
		buttonBox = QDialogButtonBox(buttons)
		buttonBox.setCenterButtons(True)
		buttonBox.accepted.connect(lambda: self.predone(1))
		buttonBox.rejected.connect(lambda: self.predone(0))
		layout.addWidget(buttonBox, current_row, 0, 1, columns)
		self.update()
	
	def update(self):
		for source in ("probe", "pump"):
			widgets = self.widgets.get(source)
			if widgets:
				measurements = widgets["measurements"].value()
				if "center" in widgets and "span" in widgets:
					center, span = widgets["center"].value(), widgets["span"].value()
					start_total, stop_total = center - span * 0.5, center + span * (measurements - 0.5)
				else:
					start, stop = widgets["start"].value(), widgets["stop"].value()
					span = (stop - start)
					start_total, stop_total = start, start + span * measurements
					
				widgets["starttotal"].setValue(start_total)
				widgets["stoptotal"].setValue(stop_total)

	def save(self):
		result = {}
		for source in ("probe", "pump"):
			widgets = self.widgets.get(source)
			if widgets:
				measurements = widgets["measurements"].value()
			else:
				measurements = 1
			result[source] = measurements
		
		return(result)

	def predone(self, val):
		mw.config["batchdialog_width"] =	self.geometry().width()
		mw.config["batchdialog_height"] =	self.geometry().height()
		self.done(val)

class GeneralWindow(MeasWidget):
	def __init__(self, parent):
		self.gridpos = (0, 1)
		self.title = "General"
		self.widgets = {
			"Mode":					QQ(QComboBox, "general_mode", options=devices.modes),
			"User":					QQ(QLineEdit, "general_user"),
			"Molecule":				QQ(QLineEdit, "general_molecule"),
			"Molecule Formula":		QQ(QLineEdit, "general_chemicalformula"),
			"Project":				QQ(QLineEdit, "general_project"),
			"Comment":				QQ(QLineEdit, "general_comment"),
			"Notification":			QQ(QBoolComboBox, "general_sendnotification"),
			"Notification Address":	QQ(QLineEdit, "general_notificationaddress"),
			"DM Jump":				QQ(QDoubleSpinBox, "general_dmjump", range=(0, None)),
			"DM Period":			QQ(QDoubleSpinBox, "general_dmperiod", range=(0, None)),
		}
		return super().__init__(parent)

class StaticWindow(MeasWidget):
	def __init__(self, parent):
		self.gridpos = (0, 0)
		self.title = "Static Values"
		self.widgets = {
			"Probe Device":			QQ(QComboBox, "static_probedevice", options=devices.deviceclasses["probe"].keys()),
			"Probe Multiplication":	QQ(QSpinBox, "static_probemultiplication", range=(1, None)),
			"Probe Address":		QQ(QLineEdit, "static_probeaddress"),
			"LockIn Device":		QQ(QComboBox, "static_lockindevice", options=devices.deviceclasses["lockin"].keys()),
			"LockIn Address":		QQ(QLineEdit, "static_lockinaddress"),
			"Pump Device":			QQ(QComboBox, "static_pumpdevice", options=devices.deviceclasses["pump"].keys()),
			"Pump Multiplication":	QQ(QSpinBox, "static_pumpmultiplication", range=(1, None)),
			"Pump Address":			QQ(QLineEdit, "static_pumpaddress"),
		}
		return super().__init__(parent)

class ProbeWindow(MeasWidget):
	def __init__(self, parent):
		self.gridpos = (1, 1)
		self.title = "Probe"
		self.widgets = {
			"Power":				QQ(QSpinBox, "probe_power", range=(1, None)),
			"Frequency":			QQ(QSweep, "probe_frequency"),
		}
		return super().__init__(parent)
	
class PumpWindow(MeasWidget):
	def __init__(self, parent):
		self.gridpos = (1, 2)
		self.title = "Pump"
		self.widgets = {
			"Power":				QQ(QSpinBox, "pump_power", range=(1, None)),
			"Frequencies":			QQ(QSweep, "pump_frequency"),
		}
		return super().__init__(parent)
	
class LockInWindow(MeasWidget):
	def __init__(self, parent):
		self.gridpos = (1, 0)
		self.title = "LockIn"
		self.widgets = {
			"FM Frequency":			QQ(QDoubleSpinBox, "lockin_fmfrequency", range=(0, None)),
			"FM Amplitude":			QQ(QDoubleSpinBox, "lockin_fmamplitude", range=(0, None)),
			"Timeconstant":			QQ(QComboBox, "lockin_timeconstant", options=devices.SignalRecovery7265.TC_OPTIONS),
			"Delay Time":			QQ(QDoubleSpinBox, "lockin_delaytime", range=(0, None)),
			"Range":				QQ(QComboBox, "lockin_sensitivity", options=devices.SignalRecovery7265.SEN_OPTIONS),
			"AC Gain":				QQ(QComboBox, "lockin_acgain", options=devices.SignalRecovery7265.ACGAIN_OPTIONS),
			"Iterations":			QQ(QDoubleSpinBox, "lockin_iterations", range=(1, None)),
		}
		return super().__init__(parent)
	
class RefillWindow(MeasWidget):
	def __init__(self, parent):
		self.gridpos = (0, 2)
		self.title = "Pressure and Refill"
		self.widgets = {
			"Gauge Address":		QQ(QLineEdit, "refill_address"),
			"Measure Pressure":		QQ(QBoolComboBox, "refill_measurepressure"),
			"Refill Cell":			QQ(QBoolComboBox, "refill_refill"),
			"Inlet Address":		QQ(QLineEdit, "refill_inletaddress"),
			"Outlet Address":		QQ(QLineEdit, "refill_outletaddress"),
			"Min Pressure":			QQ(QDoubleSpinBox, "refill_minpressure", range=(0, None)),
			"Max Pressure":			QQ(QDoubleSpinBox, "refill_maxpressure", range=(0, None)),
			"Threshold Pressure":	QQ(QDoubleSpinBox, "refill_thresholdpressure", range=(0, None)),
			"Empty Pressure":		QQ(QDoubleSpinBox, "refill_emptypressure", range=(0, None)),
			"Force Refill":			QQ(QBoolComboBox, "refill_force"),
		}
		return super().__init__(parent)

class HoverWindow(EQWidget):
	def __init__(self, parent=None):
		super().__init__("Hover", None)
		self.setWindowTitle("Hover")

		layout = QVBoxLayout()
		self.setLayout(layout)

		self.log_area = QTextEdit()
		self.log_area.setReadOnly(True)
		self.log_area.setMinimumHeight(50)

		parent.signalclass.writehover.connect(lambda text: self.log_area.setText(text))
		layout.addWidget(self.log_area)

class ConfigWindow(EQWidget):
	def __init__(self, id, parent=None):
		super().__init__(id, parent)
		self.setWindowTitle("Config")

		vbox = QVBoxLayout()
		scrollarea = QScrollArea()
		widget = QWidget()
		layout = QGridLayout()

		self.updating = True

		scrollarea.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
		scrollarea.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
		scrollarea.setWidgetResizable(True)

		tmp_layout = QHBoxLayout()
		tmp_layout.addWidget(QQ(QPushButton, text="Save as default", change=lambda: mw.saveoptions()))
		completer = QCompleter(mw.config.keys())
		completer.setCaseSensitivity(Qt.CaseInsensitive)
		tmp_layout.addWidget(QQ(QLineEdit, placeholder="Search", completer=completer, change=lambda x: self.search(x)))
		tmp_layout.addStretch(1)

		vbox.addLayout(tmp_layout)
		self.widgets = {}

		i = 1
		for key, value in mw.config.items():
			text = json.dumps(value) if isinstance(value, (dict, list, tuple)) else str(value)
			tmp_input = QQ(QLineEdit, value=text, change=lambda text, key=key: self.set_value(key, text))
			tmp_oklab = QQ(QLabel, text="Good")
			tmp_label = QQ(QLabel, text=key)

			self.widgets[key] = (tmp_input, tmp_oklab, tmp_label)
			layout.addWidget(tmp_label, i+1, 0)
			layout.addWidget(tmp_input, i+1, 1)
			layout.addWidget(tmp_oklab, i+1, 2)
			i += 1

		layout.setRowStretch(i+1, 1)

		widget.setLayout(layout)
		scrollarea.setWidget(widget)
		vbox.addWidget(scrollarea)

		self.setLayout(vbox)

		self.updating = False
		self.timer = None

	def show(self, *args, **kwargs):
		self.timer = QTimer(self)
		self.timer.timeout.connect(self.get_values)
		self.timer.start(200)
		return(super().show(*args, **kwargs))

	def search(self, text):
		for key, value in self.widgets.items():
			if text.lower() in key or text.lower() in value[0].text():
				hidden = False
			else:
				hidden = True
			value[0].setHidden(hidden)
			value[1].setHidden(hidden)
			value[2].setHidden(hidden)

	def get_values(self):
		self.updating = True
		for key, (input, oklabel, label) in self.widgets.items():
			value = mw.config[key]
			if input.hasFocus() or self.widgets[key][1].text() == "Bad":
				continue
			if isinstance(value, (dict, list, tuple)):
				input.setText(json.dumps(value))
			else:
				input.setText(str(value))
		self.updating = False

	def set_value(self, key, value):
		if self.updating:
			return
		converter = config_specs.get(key)
		if converter:
			converter = converter[1]
		input, oklab, label = self.widgets[key]

		try:
			if converter is None:
				pass
			elif converter in (dict, list, tuple):
				value = json.loads(value)
			elif converter == bool:
				value = True if value in ["True", "1"] else False
			else:
				value = converter(value)
			mw.config[key] = value
			oklab.setText("Good")
		except Exception as E:
			oklab.setText("Bad")


	def closeEvent(self, *args, **kwargs):
		if self.timer:
			self.timer.stop()
			self.timer = None
		return super().closeEvent(*args, **kwargs)



class LogWindow(EQDockWidget):
	def __init__(self, parent):
		self.dockpos = 8
		super().__init__(parent)
		self.setWindowTitle("Log")

		mainwidget = QGroupBox()
		layout = QVBoxLayout()
		self.setWidget(mainwidget)
		mainwidget.setLayout(layout)

		self.log_area = QTextEdit()
		self.log_area.setReadOnly(True)
		self.log_area.setMinimumHeight(50)

		parent.signalclass.writelog.connect(lambda text: self.writelog(text))
		layout.addWidget(self.log_area)

	def writelog(self, text):
		tmp = self.log_area.toPlainText()
		tmp = tmp.split("\n")
		if len(tmp)-1 > mw.config["flag_logmaxrows"]:
			self.log_area.setText("\n".join(tmp[-mw.config["flag_logmaxrows"]:]))

		self.log_area.append(text)
		sb = self.log_area.verticalScrollBar()
		sb.setValue(sb.maximum())

class FileWindow(EQWidget):
	def __init__(self, id, parent=None):
		super().__init__(id, parent)
		self.setWindowTitle("Files Window")

		self.tabs = QTabWidget()
		tmplayout = QVBoxLayout()
		tmplayout.addWidget(self.tabs)
		self.setLayout(tmplayout)

		keys = ("exp", "cat", "lin")
		self.widgets = {key: {} for key in keys}
		self.layouts = {key: self.create_layout(key, initial=True) for key in keys}
		for label, layout in self.layouts.items():
			tmpwidget = QWidget()
			tmpwidget.setLayout(layout)
			self.tabs.addTab(tmpwidget, label.capitalize())

		mw.signalclass.fileschanged.connect(self.update)

	def update(self, type=None):
		if type is None:
			types = ("exp", "cat", "lin")
		else:
			types = (type, )


		for type in types:
			filesgrid = self.widgets[f"{type}_filesgrid"]

			scrollarea = self.widgets.get(f"{type}_scrollarea")
			if scrollarea:
				tmp = (scrollarea.verticalScrollBar().value(), scrollarea.horizontalScrollBar().value())
			else:
				tmp = (0, 0)

			# Delete existing widgets
			for key, value in self.widgets[type].items():
				if not (key.startswith("__") and key.endswith("__")):
					for widget in value.values():
						widget.deleteLater()

			self.widgets[type] = {key: value for key, value in self.widgets[type].items() if (key.startswith("__") and key.endswith("__"))}

			if type == "exp":
				actions = ("label", "colorinput", "colorpicker", "scale", "hide", "delete", "reread")
			elif type == "cat":
				actions = ("label", "colorinput", "colorpicker", "scale", "hide", "delete", "reread")
			elif type == "lin":
				actions = ("label", "colorinput", "colorpicker", "hide", "delete", "reread")

			row_id = 0
			files = mw.config[f"files_{type}"]

			for file in files:
				self.add_row(filesgrid, type, file, actions, row_id)
				row_id += 1

			filesgrid.setRowStretch(row_id, 1)

			scrollarea.verticalScrollBar().setValue(tmp[0])
			scrollarea.horizontalScrollBar().setValue(tmp[1])


	def create_layout(self, type, initial=False):
		if initial:
			layout = QVBoxLayout()

			buttonsbox = QHBoxLayout()
			scrollarea = QScrollArea()
			widget = QWidget()

			layout.addLayout(buttonsbox)

			buttonsbox.addWidget(QQ(QToolButton, text="Load", change=lambda x, type=type: mw.load_file(type, add_files=True, keep_old=False)))
			buttonsbox.addWidget(QQ(QToolButton, text="Add", change=lambda x, type=type: mw.load_file(type, add_files=True, keep_old=True)))
			buttonsbox.addWidget(QQ(QToolButton, text="Reread All", change=lambda x, type=type: mw.load_file(type, reread=True, do_QNs=False)))
			buttonsbox.addWidget(QQ(QToolButton, text="Reset All", change=lambda x, type=type: self.reset_all(type)))
			buttonsbox.addWidget(QQ(QToolButton, text="Delete All", change=lambda x, type=type: self.delete_file(type)))
			buttonsbox.addStretch(1)

			filesgrid = QGridLayout()

			scrollarea.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
			scrollarea.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
			scrollarea.setWidgetResizable(True)
			scrollarea.setWidget(widget)
			widget.setLayout(filesgrid)

			self.widgets[f"{type}_filesgrid"] = filesgrid
			self.widgets[f"{type}_scrollarea"] = scrollarea

			topbox = QHBoxLayout()
			layout.addLayout(topbox)

			color = mw.config.get(f"color_{type}")
			file = f"__{type}__"

			rowdict = {
				"label":			QQ(QLabel, text="Initial Color"),
				"colorinput":		QQ(QLineEdit, text=color, maxWidth=200, change=lambda x, file=file, type=type: self.change_color(type, file, inp=True)),
				"colorpicker":		QQ(QToolButton, text="CP", change=lambda x, file=file, type=type: self.change_color(type, file), stylesheet=f"background-color: {rgbt_to_trgb(color)}"),
			}
			for label, widget in rowdict.items():
				topbox.addWidget(widget)
			self.widgets[type][file] = rowdict


			layout.addWidget(scrollarea, 10)

		self.update(type)

		return(layout)

	def add_row(self, layout, type, file, actions, row_id):
		file_options = mw.config[f"files_{type}"][file]
		color = file_options.get("color", "#ffffff")
		hidden = file_options.get("hidden")
		scale = file_options.get("scale", 1)

		rowdict = {
			"label":			QQ(QLabel, text=file, enabled=not hidden),
			"scale":			QQ(QDoubleSpinBox, value=scale, range=(None, None), change=lambda x, file=file, type=type: self.scale_file(type, file, x)),
			"colorinput":		QQ(QLineEdit, text=color, maxWidth=200, change=lambda x, file=file, type=type: self.change_color(type, file, inp=True)),
			"colorpicker":		QQ(QToolButton, text="CP", change=lambda x, file=file, type=type: self.change_color(type, file), stylesheet=f"background-color: {rgbt_to_trgb(color)}"),
			"hide":				QQ(QToolButton, text="Show" if hidden else "Hide", change=lambda x, type=type, file=file: self.hide_file(type, file)),
			"delete":			QQ(QToolButton, text="×", change=lambda x, type=type, file=file: self.delete_file(type, file), tooltip="Delete file"),
			"reread":			QQ(QToolButton, text="⟲", change=lambda x, type=type, file=file: mw.load_file(type, add_files=[file], keep_old=True, do_QNs=False), tooltip="Reread File"),
		}

		for col_id, action in enumerate(actions):
			layout.addWidget(rowdict[action], row_id, col_id)
			layout.setRowStretch(row_id, 0)

		self.widgets[type][file] = rowdict

	def scale_file(self, type, file, scale):
		mw.config[f"files_{type}"][file]["scale"] = scale
		mw.plotwidget.set_data()

	def reset_all(self, type):
		files = mw.config[f"files_{type}"]

		for file in files:
			if "scale" in files[file]:
				files[file]["scale"] = 1

			if "hidden" in files[file]:
				files[file]["hidden"] = False

			if "color" in files[file]:
				files[file]["color"] = mw.config[f"color_{type}"]

		mw.signalclass.fileschanged.emit()

	@working_d
	def delete_file(self, type, file=None):
		df = mw.return_df(type)

		with locks[f"{type}_df"]:
			if file is None:
				mw.config[f"files_{type}"].clear()
				df.drop(df.index, inplace=True)
			else:
				if file in mw.config[f"files_{type}"]:
					del mw.config[f"files_{type}"][file]
				df.drop(df[df["filename"]==file].index, inplace=True)

		mw.load_file(type, keep_old=True, do_QNs=False)

	@synchronized_d(locks["axs"])
	def hide_file(self, type, file):
		hidden = mw.config[f"files_{type}"][file].get("hidden", False)
		hidden = not hidden
		mw.config[f"files_{type}"][file]["hidden"] = hidden


		if hidden:
			self.widgets[type][file]["label"].setEnabled(False)
			self.widgets[type][file]["hide"].setText("Show")
		else:
			self.widgets[type][file]["label"].setEnabled(True)
			self.widgets[type][file]["hide"].setText("Hide")

		mw.signalclass.updateplot.emit()

	@synchronized_d(locks["axs"])
	def change_color(self, type, file, inp=False):
		color_input = self.widgets[type][file]["colorinput"].text()
		if inp:
			color = color_input
		else:
			color = QColorDialog.getColor(initial=QColor(rgbt_to_trgb(color_input)), options=QColorDialog.ShowAlphaChannel)
			if color.isValid():
				color = trgb_to_rgbt(color.name(QColor.HexArgb))
			else:
				return

		try:
			color = Color(color)
		except CustomError:
			return

		self.widgets[type][file]["colorpicker"].setStyleSheet(f"background-color: {rgbt_to_trgb(color)}")
		if self.widgets[type][file]["colorinput"].text() != color:
			self.widgets[type][file]["colorinput"].setText(color)

		if file.startswith("__") and file.endswith("__"):
			tmp = file.strip("_")
			mw.config[f"color_{tmp}"] = color
		else:
			mw.config[f"files_{type}"][file]["color"] = color
		mw.signalclass.updateplot.emit()

class CreditsWindow(EQWidget):
	def __init__(self, id, parent=None):
		super().__init__(id, parent)
		self.setWindowTitle("Credits")

		global CREDITSSTRING
		layout = QVBoxLayout()
		layout.addWidget(QQ(QLabel, text=CREDITSSTRING, align=Qt.AlignCenter, wordwrap=True, minHeight=300, minWidth=500))
		self.setLayout(layout)


class ConsoleDialog(QDialog):
	def __init__(self):
		super().__init__()
		QShortcut("Esc", self).activated.connect(lambda: self.predone(0))

		self.setWindowTitle(f"Command Line Dialog")
		self.resize(mw.config["commandlinedialog_width"], mw.config["commandlinedialog_height"])

		self.tabs = QTabWidget()
		self.tabs.setTabsClosable(True)
		self.tabs.setMovable(True)
		self.tabs.setDocumentMode(True)

		initial_values = mw.config["commandlinedialog_commands"]
		if initial_values:
			for title, command in initial_values:
				self.add_tab(title, command)
		else:
			self.add_tab()

		self.tabs.tabCloseRequested.connect(self.close_tab)
		self.tabs.tabBarDoubleClicked.connect(self.renameoradd_tab)
		self.tabs.setCurrentIndex(mw.config["commandlinedialog_current"])

		layout = QVBoxLayout()
		self.setLayout(layout)

		layout.addWidget(self.tabs)
		buttons_layout = QHBoxLayout()
		buttons_layout.addStretch()
		buttons_layout.addWidget(QQ(QPushButton, text="Run", change=lambda x: self.predone(1), shortcut="Ctrl+Return"))
		buttons_layout.addWidget(QQ(QPushButton, text="Cancel", change=lambda x: self.predone(0), shortcut="Esc"))
		buttons_layout.addStretch()
		layout.addLayout(buttons_layout)

	def add_tab(self, title="Command", command=""):
		textarea = QQ(QPlainTextEdit, value=command)
		cursor = textarea.textCursor()
		cursor.movePosition(QTextCursor.End)
		textarea.setTextCursor(cursor)
		self.tabs.addTab(textarea, title)

	def close_tab(self, index):
		tab = self.tabs.widget(index)
		tab.deleteLater()
		self.tabs.removeTab(index)
		if self.tabs.count() == 0:
			self.add_tab()

	def renameoradd_tab(self, index):
		if index == -1:
			self.add_tab()
		elif self.tabs.widget(index) != 0:
			text, ok = QInputDialog().getText(self, "Tab Name","Enter the Tabs Name:")
			if ok and text:
				self.tabs.setTabText(index, text)

	def predone(self, val):
		commands = []
		for i in range(self.tabs.count()):
			tab = self.tabs.widget(i)
			title = self.tabs.tabText(i)
			command = tab.toPlainText()
			commands.append((title, command))

		mw.config["commandlinedialog_commands"] = commands
		mw.config["commandlinedialog_current"] = self.tabs.currentIndex()

		mw.config["commandlinedialog_width"] =	self.geometry().width()
		mw.config["commandlinedialog_height"] =	self.geometry().height()
		self.done(val)

	def run(showdialog=True):
		if showdialog:
			dialog = ConsoleDialog()
			dialog.exec_()
			if dialog.result() != 1:
				return

		title, command = mw.config["commandlinedialog_commands"][mw.config["commandlinedialog_current"]]
		if not command.strip():
			return
		message = []
		old_stdout = sys.stdout
		red_output = sys.stdout = io.StringIO()
		try:
			exec(command)
		except Exception as E:
			message.append(f"<span style='color:#eda711;'>WARNING</span>: Executing the code raised an error: {str(E)}")
			raise
		finally:
			sys.stdout = old_stdout

		message.append("\n".join([f">>> {line}" for line in command.split("\n")]))
		message.append(red_output.getvalue())
		mw.notification("\n".join(message))

##
## Global Functions
##
def QQ(widgetclass, config_key=None, config_object=None, **kwargs):
	widget = widgetclass()
	if config_object is None:
		config_object = mw.config

	if "range" in kwargs:
		widget.setRange(*kwargs["range"])
	if "maxWidth" in kwargs:
		widget.setMaximumWidth(kwargs["maxWidth"])
	if "maxHeight" in kwargs:
		widget.setMaximumHeight(kwargs["maxHeight"])
	if "minWidth" in kwargs:
		widget.setMinimumWidth(kwargs["minWidth"])
	if "minHeight" in kwargs:
		widget.setMinimumHeight(kwargs["minHeight"])
	if "color" in kwargs:
		widget.setColor(kwargs["color"])
	if "text" in kwargs:
		widget.setText(kwargs["text"])
	if "options" in kwargs:
		options = kwargs["options"]
		if isinstance(options, dict):
			for key, value in options.items():
				widget.addItem(value)
		else:
			for option in kwargs["options"]:
				widget.addItem(option)
	if "width" in kwargs:
		widget.setFixedWidth(kwargs["width"])
	if "tooltip" in kwargs:
		widget.setToolTip(kwargs["tooltip"])
	if "placeholder" in kwargs:
		widget.setPlaceholderText(kwargs["placeholder"])
	if "singlestep" in kwargs:
		widget.setSingleStep(kwargs["singlestep"])
	if "wordwrap" in kwargs:
		widget.setWordWrap(kwargs["wordwrap"])
	if "align" in kwargs:
		widget.setAlignment(kwargs["align"])
	if "rowCount" in kwargs:
		widget.setRowCount(kwargs["rowCount"])
	if "columnCount" in kwargs:
		widget.setColumnCount(kwargs["columnCount"])
	if "move" in kwargs:
		widget.move(*kwargs["move"])
	if "default" in kwargs:
		widget.setDefault(kwargs["default"])
	if "textFormat" in kwargs:
		widget.setTextFormat(kwargs["textFormat"])
	if "checkable" in kwargs:
		widget.setCheckable(kwargs["checkable"])
	if "shortcut" in kwargs:
		widget.setShortcut(kwargs["shortcut"])
	if "parent" in kwargs:
		widget.setParent(kwargs["parent"])
	if "completer" in kwargs:
		widget.setCompleter(kwargs["completer"])
	if "hidden" in kwargs:
		widget.setHidden(kwargs["hidden"])
	if "visible" in kwargs:
		widget.setVisible(kwargs["visible"])
	if "stylesheet" in kwargs:
		widget.setStyleSheet(kwargs["stylesheet"])
	if "enabled" in kwargs:
		widget.setEnabled(kwargs["enabled"])
	if "items" in kwargs:
		for item in kwargs["items"]:
			widget.addItem(item)
	if "readonly" in kwargs:
		widget.setReadOnly(kwargs["readonly"])
	if "prefix" in kwargs:
		widget.setPrefix(kwargs["prefix"])

	if widgetclass in [QSpinBox, QDoubleSpinBox]:
		setter = widget.setValue
		changer = widget.valueChanged.connect
		getter = widget.value
	elif widgetclass == QCheckBox:
		setter = widget.setChecked
		changer = widget.stateChanged.connect
		getter = widget.isChecked
	elif widgetclass == QPlainTextEdit:
		setter = widget.setPlainText
		changer = widget.textChanged.connect
		getter = widget.toPlainText
	elif widgetclass == QLineEdit:
		setter = widget.setText
		changer = widget.textChanged.connect
		getter = widget.text
	elif widgetclass == QAction:
		setter = widget.setChecked
		changer = widget.triggered.connect
		getter = widget.isChecked
	elif widgetclass == QPushButton:
		setter = widget.setDefault
		changer = widget.clicked.connect
		getter = widget.isDefault
	elif widgetclass == QToolButton:
		setter = widget.setChecked
		changer = widget.clicked.connect
		getter = widget.isChecked
	elif widgetclass in [QComboBox, QBoolComboBox]:
		setter = widget.setCurrentText
		changer = widget.currentTextChanged.connect
		getter = widget.currentText
	elif widgetclass == QSweep:
		setter = widget.setState
		changer = widget.changed.connect
		getter = widget.getState
	else:
		return widget

	if "value" in kwargs:
		setter(kwargs["value"])
	if config_key:
		setter(config_object[config_key])
		changer(lambda x=None, key=config_key: config_object.__setitem__(key, getter(), widget))
		config_object.register_widget(config_key, widget, lambda: setter(config_object[config_key]))
	if "change" in kwargs:
		changer(kwargs["change"])
	if "changes" in kwargs:
		for change in kwargs["changes"]:
			changer(change)

	return widget

def exp_to_df(fname, sep="\t", xcolumn=0, ycolumn=1, sort=True):
	data = pd.read_csv(fname, sep=sep, dtype=np.float64, header=None, engine="c", comment="#")
	column_names = [i for i in range(len(data.columns))]

	column_names[xcolumn if xcolumn in column_names else 0] = "x"
	column_names[ycolumn if ycolumn in column_names else 1] = "y"

	data.columns = column_names
	data = data[["x", "y",]]
	data["filename"] = fname

	return(data)

def create_colors(dataframe, files={}):
	if len(files) == 1:
		return(files[list(files.keys())[0]].get("color", "#ffffff"))
	if not isinstance(dataframe, pd.DataFrame):
		return([])

	dataframe.reset_index(drop=True, inplace=True)
	tmp_colors = {file: files[file].get("color", "#ffffff") for file in files.keys()}
	filenames = dataframe["filename"]
	colors = filenames.replace(tmp_colors).copy()

	return(colors)

def bin_data(dataframe, binwidth, range):
	length = len(dataframe)

	dataframe.loc[:,"bin"] = (dataframe.loc[:,"x"]-range[0]) // binwidth
	dataframe = dataframe.loc[dataframe.sort_values("y").drop_duplicates(("bin", "filename"), keep="last").sort_values(["x"]).index]
	return(dataframe)

def symmetric_ticklabels(ticks):
	tick_labels = []
	for a, o in zip(ticks, ticks[::-1]):
		if not (np.isfinite(a) and np.isfinite(o)):
			continue
		dec_a = len(f"{a:.4f}".rstrip("0").split(".")[1])
		dec_o = len(f"{o:.4f}".rstrip("0").split(".")[1])
		if dec_a == dec_o:
			tick_labels.append(f"{a:.4f}".rstrip("0").rstrip("."))
		else:
			trailing_zeros = 4 - max(dec_a, dec_o)
			tick = f"{a:.4f}"[:-trailing_zeros] if trailing_zeros else f"{a:.4f}"
			tick_labels.append(tick)
	return(tick_labels)

def except_hook(cls, exception, traceback):
	if issubclass(cls, KeyboardInterrupt):
		sys.exit(0)

	sys.__excepthook__(cls, exception, traceback)
	with open(customfile(".err"), "a+", encoding="utf-8") as file:
		time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
		file.write(f"{time_str}: \n{exception}\n{''.join(tb.format_tb(traceback))}\n\n")
	try:
		mw.notification(f"{exception}\n{''.join(tb.format_tb(traceback))}")
	except Exception as E:
		pass

def customfile(extension):
	# Using the folder the python file is in causes problems with the Exe file
	# as it unpacks the python stuff into a temporary folder
	# return(os.path.join(os.path.dirname(os.path.realpath(__file__)), f"{APP_TAG}{extension}"))
	return(f"..\logs\{APP_TAG}{extension}")

def breakpoint(ownid, lastid):
	if ownid != lastid:
		raise CustomError()

def encodebytes(bytes):
	return ", ".join([str(x) for x in bytes])

def decodebytes(code):
	return bytearray([int(x) for x in code.split(", ")])

def trgb_to_rgbt(color):
	if len(color) == 9:
		color = f"#{color[3:]}{color[1:3]}"
	return(color)

def rgbt_to_trgb(color):
	if len(color) == 9:
		color = f"#{color[-2:]}{color[1:-2]}"
	return(color)

def restart():
	ws.close()
	mw.saveoptions()
	os.execv(sys.executable, [sys.executable, sys.argv[0]])

##
## Global Variables
##

cat_dtypes = pyckett.cat_dtypes
lin_dtypes = pyckett.lin_dtypes
exp_dtypes = {
  'x':			np.float64,
  'y':			np.float64,
}

# Format is: [default value, class]
config_specs = {
	"general_mode":							[devices.modes[0], str],
	"general_user":							["", str],
	"general_molecule":						["", str],
	"general_chemicalformula":				["", str],
	"general_project":						["", str],
	"general_comment":						["", str],
	"general_sendnotification":				[False, bool],
	"general_notificationaddress":			["", str],
	"general_dmjump":						[120, float],
	"general_dmperiod":						[5, float],
	
	"static_probeaddress":					["", str],
	"static_probedevice":					["MockDevice", str],
	"static_probemultiplication":			[1, int],
	"static_lockinaddress":					["", str],
	"static_lockindevice":					["MockDevice", str],
	"static_pumpaddress":					["", str],
	"static_pumpdevice":					["MockDevice", str],
	"static_pumpmultiplication":			[1, int],
	"static_lockinreadmag":					[False, bool],
	"static_skipreset":						[False, bool],
	
	"probe_power":							[10, int],
	"probe_frequency":						[{
												"mode": "sweep",
												"iterations": 1,
												"direction": "forthback",
												"center": 69000, 
												"span": 4.20, 
												"points": 1000,
											}, dict],
										
	"pump_power":							[10, int],
	"pump_frequency":						[{
												"mode": "fixed",
												"center": 19840,
											}, dict],
	
	"lockin_fmfrequency":					[27613, float],
	"lockin_fmamplitude":					[180, float],
	"lockin_timeconstant":					["20ms", str],
	"lockin_delaytime":						[25, float],
	"lockin_sensitivity":					["500mV", str],
	"lockin_acgain":						["0dB", str],
	"lockin_iterations":					[1, int],
	
	"refill_refill":						[False, bool],
	"refill_measurepressure":				[True, bool],
	"refill_address": 						["", str],
	"refill_inletaddress": 					["", str],
	"refill_outletaddress": 				["", str],
	"refill_minpressure": 					[0, float],
	"refill_maxpressure": 					[0, float],
	"refill_thresholdpressure": 			[0, float],
	"refill_emptypressure": 				[0, float],
	"refill_force": 						[False, bool],
	
	"layout_theme":							["light", str],
	"layout_owntheme":						[{}, dict],
	"layout_mpltoolbar":					[False, bool],

	"color_exp":							["#000000", Color],
	"color_lin":							["#ff38fc", Color],
	"color_cat":							["#d91e6f", Color],
	"color_cur":							["#71eb34", Color],
	"color_fit":							["#bc20e3", Color],
	"color_meas":							["#ff000099", Color],

	"plot_dpi":								[100, float],
	"plot_ymargin":							[0.1, float],
	"plot_hover_cutoff":					[20, float],
	"plot_ticks":							[3, int],
	"plot_scientificticks":					[0, int],
	"plot_autoscale":						[True, bool],
	"plot_expcat_factor":					[1, float],
	"plot_expcat_exponent":					[10, int],
	"plot_yscale_min":						[-100, float],
	"plot_yscale_max":						[300, float],
	"plot_bins":							[4000, int],
	"plot_skipbinning":						[1000, int],
	"plot_expasstickspectrum":				[False, bool],

	"flag_qns":								[3, int],
	"flag_automatic_draw":					[True, bool],
	"flag_xcolumn":							[0, int],
	"flag_ycolumn":							[1, int],
	"flag_separator":						[9, int],
	"flag_debug":							[False, bool],
	"flag_alwaysshowlog":					[True, bool],
	"flag_extensions":						[{"exp": [".csv"], "cat": [".cat"], "lin": [".lin"], "measurement": [".meas"]}, dict],
	"flag_predictionformats":				[{}, dict, True],
	"flag_assignmentformats":				[{}, dict, True],
	"flag_loadfilesthreaded":				[True, bool],
	"flag_shownotification":				[True, bool],
	"flag_notificationtime":				[2000, int],
	"flag_showmainplotcontrols":			[True, bool],
	"flag_showmainplotposition":			[True, bool],
	"flag_logmaxrows":						[10000, int],
	"flag_updateplot":						[200, int],

	"commandlinedialog_width":				[500, int],
	"commandlinedialog_height":				[250, int],
	"commandlinedialog_commands":			[[], list],
	"commandlinedialog_current":			[1, int],

	"batchdialog_width":					[1000, int],
	"batchdialog_height":					[500, int],

	"files_exp":							[{}, dict],
	"files_cat":							[{}, dict],
	"files_lin":							[{}, dict],
}

if __name__ == '__main__':
	sys.excepthook = except_hook
	threading.excepthook = lambda args: except_hook(*args[:3])

	app = QApplication(sys.argv)
	app.setStyle("Fusion")
	mw = MainWindow()
	ws = Websocket()
	sys.exit(app.exec_())
