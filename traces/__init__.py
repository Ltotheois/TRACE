def start():
	try:
		import subprocess
		
		popen_kwargs = {
			"creationflags": subprocess.CREATE_NEW_CONSOLE,
		}
		subprocess.Popen("trace_gui", **popen_kwargs)
		subprocess.Popen("trace_exp", **popen_kwargs)
	except Exception as E:
		print(E)
		input("Startup failed. Press any key to exit.")

if __name__ == '__main__':
	start()