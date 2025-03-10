import sys
import subprocess
import re
import logging
import gi

from typing import Union, Tuple, Callable, Dict

gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, Gio


def run_efibootmgr():
	output = subprocess.run(["efibootmgr", "-v"], check=True, capture_output=True,
				text=True).stdout.strip().split('\n')
	logging.debug(repr(output))
	return output


def btn_with_icon(icon):
	btn = Gtk.Button(hexpand=True)
	icon = Gio.ThemedIcon(name=icon)
	image = Gtk.Image.new_from_gicon(icon)
	btn.set_child(image)
	return btn


def yes_no_dialog(parent, primary, secondary, on_response):
	dialog = Gtk.MessageDialog(transient_for=parent, message_type=Gtk.MessageType.QUESTION,
							buttons=Gtk.ButtonsType.YES_NO, text=primary, secondary_text=secondary)
	area = dialog.get_message_area()
	child = area.get_first_child()
	while child:
		child.set_selectable(True)
		child = child.get_next_sibling()
	dialog.connect('response', on_response)
	dialog.show()
	return dialog


def error_dialog(transient_for: Gtk.Window, message: str, title: str, on_response):
	dialog = Gtk.MessageDialog(transient_for=transient_for, destroy_with_parent=True,
			message_type=Gtk.MessageType.ERROR, buttons=Gtk.ButtonsType.CANCEL,
			text=title, secondary_text=message, modal=True)
	area = dialog.get_message_area()
	child = area.get_first_child()
	while child:
		child.set_selectable(True)
		child = child.get_next_sibling()
	dialog.connect('response', on_response)
	dialog.show()
	return dialog


many_esps_error_message = """
This program detected more than one EFI System Partition on your system. You have to choose the right one.
You can either mount your ESP on /boot/efi or pass the ESP block device via --efi (e.g. --efi=/dev/sda1).

Choose wisely.
"""


device_regex = re.compile(r'^([a-z/]+[0-9a-z]*?)p?([0-9]+)$')

def device_to_disk_part(device: str) -> Tuple[str, str]:
	try:
		disk, part = device_regex.match(device).groups()
		logging.debug("Device path %s split into %s and %s", device, disk, part)
		return disk, part
	except AttributeError:
		raise ValueError("Could not match device " + device)


def make_auto_detect_esp_with_findmnt(esp_mount_point) -> Callable:
	def auto_detect_esp_with_findmnt() -> Tuple[str, str]:
		# findmnt --noheadings --output SOURCE --mountpoint /boot/efi
		cmd = ["findmnt", "--noheadings", "--output", "SOURCE,FSTYPE", "--mountpoint", esp_mount_point]

		logging.debug("Running: %s", ' '.join(cmd))
		findmnt_output = subprocess.run(cmd, check=True, capture_output=True, text=True).stdout
		splitted = findmnt_output.strip().split()
		for source, fstype in zip(splitted[::2], splitted[1::2]):
			if fstype == 'vfat':
				disk, part = device_to_disk_part(source)
				return disk, part
	return auto_detect_esp_with_findmnt


def auto_detect_esp_with_lsblk() -> Tuple[str, str]:
	"""
	Finds the ESP by scanning the partition table. It should work with GPT (tested) and MBR (not tested).
	This method doesn't require the ESP to be mounted.
	:return: 2 strings that can be passed to efibootmgr --disk and --part argument.
	"""
	esp_part_types = ('C12A7328-F81F-11D2-BA4B-00A0C93EC93B', 'EF')

	# lsblk --noheadings --pairs --paths --output NAME,PARTTYPE
	cmd = ['lsblk', '--noheadings', '--pairs', '--paths', '--output', 'NAME,PARTTYPE,FSTYPE']

	logging.debug("Running: %s", ' '.join(cmd))
	res = subprocess.run(cmd, check=True, capture_output=True, text=True).stdout.strip()
	regex = re.compile('^NAME="(.+)" PARTTYPE="(.+)" FSTYPE="(.+)"$', re.MULTILINE)
	esps = []
	for match in regex.finditer(res):
		name, part_type, fs_type = match.groups()
		if part_type.upper() in esp_part_types and fs_type == 'vfat':
			esps.append(name)
	if len(esps) == 1:
		source = esps[0]
		disk, part = device_to_disk_part(source)
	else:
		logging.warning(many_esps_error_message)
		error_dialog(None, f"{many_esps_error_message}\nDetected ESPs: {', '.join(esps)}",
					"More than one EFI System Partition detected!", lambda *_: sys.exit(-1))
		return None
	return disk, part


def auto_detect_esp():
	methods = (make_auto_detect_esp_with_findmnt('/efi'), make_auto_detect_esp_with_findmnt('/boot/efi'),
			   make_auto_detect_esp_with_findmnt('/boot'), auto_detect_esp_with_lsblk)
	for find_esp_method in methods:
		try:
			result = find_esp_method()
			if not result:
				continue
			disk, part = result
			logging.info("Detected ESP on disk %s part %s", disk, part)
			return disk, part
		except subprocess.CalledProcessError:
			pass
	logging.fatal("Can't auto-detect ESP! All methods failed.")
	error_dialog(None, "Could not find an EFI System Partition. Ensure your ESP is mounted on /efi, "
					"/boot/efi or /boot, that it has the correct partition type and vfat file system.",
					"Can't auto-detect ESP!", lambda *_: sys.exit(-1))


efibootmgr_regex = re.compile(r'^Boot([0-9A-F]+)(\*)? (.+)\t(?:.+\/File\((.+)\)|.*\))(.*)$')


def try_decode_efibootmgr(code):
	if '.' not in code:
		return code
	if code.endswith('.') and code.count('.') == 1:
	    return code
	if code.startswith('WINDOWS'):
		return 'WINDOWS' + try_decode_efibootmgr(code[len('WINDOWS'):])
	try:
		# Decode as UTF-16 (why efibootmgr displays it like that?)
		code_bytes = bytearray(code, 'utf-8')
		for i, byte in enumerate(code_bytes):
			if i % 2 == 1 and byte == ord('.'):
				code_bytes[i] = 0
		decoded = code_bytes.decode('utf-16')
		return decoded
	except UnicodeDecodeError as e:
		logging.warning("Could not decode '%s': %s", code, e)
		return code


def parse_efibootmgr_line(line: str) -> Tuple:
	parser_logger = logging.getLogger("parser")
	match = efibootmgr_regex.match(line)

	if match and match.group(1) and match.group(3):
		num, active, name, path, params = match.groups()
		params = try_decode_efibootmgr(params)
		parsed = dict(num=num, active=active is not None, name=name,
					path=path if path else '', parameters=params)
		parser_logger.debug("Entry: %s", parsed)
		return 'entry', parsed
	if line.startswith("BootOrder"):
		parsed = line.split(':')[1].strip().split(',')
		parser_logger.debug("BootOrder: %s", parsed)
		return 'boot_order', parsed
	if line.startswith("BootNext"):
		parsed = line.split(':')[1].strip()
		parser_logger.debug("BootNext: %s", parsed)
		return 'boot_next', parsed
	if line.startswith("BootCurrent"):
		parsed = line.split(':')[1].strip()
		parser_logger.debug("BootCurrent: %s", parsed)
		return 'boot_current', parsed
	if line.startswith("Timeout"):
		parsed = int(line.split(':')[1].split()[0].strip())
		parser_logger.debug("Timeout: %s", parsed)
		return 'timeout', parsed

	raise ValueError("line didn't match", repr(line))


def parse_efibootmgr(boot) -> Dict:
	parser_logger = logging.getLogger("parser")
	parsed_efi = {
		'entries': [],
		'boot_order': [],
		'boot_next': None,
		'boot_current': None,
		'timeout': None
	}

	for line in boot:
		try:
			key, value = parse_efibootmgr_line(line)
			if key == 'entry':
				parsed_efi['entries'].append(value)
			else:
				parsed_efi[key] = value
		except ValueError as e:
			parser_logger.warning("line didn't match: %s", e.args[1])

	return parsed_efi


class EFIStore(Gtk.ListStore):
	ROW_CURRENT = 0
	ROW_NUM = 1
	ROW_NAME = 2
	ROW_PATH = 3
	ROW_PARAMETERS = 4
	ROW_ACTIVE = 5
	ROW_NEXT = 6

	def __init__(self, window, esp):
		self.window = window
		self.esp = esp
		Gtk.ListStore.__init__(self, bool, str, str, str, str, bool, bool)
		self.regex = re.compile(r'^Boot([0-9A-F]+)(\*)? (.+)\t(?:.+File\((.+)\))?.*$')
		self.clear()

	def reorder(self):
		reorder_logger = logging.getLogger("reorder")
		new_order = []

		for bootnum in self.boot_order:
			index = self.index_num(bootnum)
			if index is None:
				reorder_logger.warning('%s is in BootOrder, but it\'s not in the list', bootnum)
			else:
				new_order.append(index)

		for i,row in enumerate(self):
			if i not in new_order:
				reorder_logger.warning('%s is not in BootOrder, appending to the list', row[EFIStore.ROW_NUM])
				new_order.append(i)

		reorder_logger.debug("New order is: %s", new_order)

		assert(len(new_order) == len(self))
		super().reorder(new_order)

	def swap(self, a, b):
		super().swap(a, b)
		self.boot_order = [ x[EFIStore.ROW_NUM] for x in self ]

	def index_num(self, num):
		for i,row in enumerate(self):
			if row[EFIStore.ROW_NUM] == num:
				return i

	def clear(self):
		super().clear()
		self.boot_order = []
		self.boot_order_initial = []
		self.boot_next = None
		self.boot_next_initial = None
		self.boot_active = []
		self.boot_inactive = []
		self.boot_add = []
		self.boot_remove = []
		self.boot_current = None
		self.timeout = None
		self.timeout_initial = None

	def refresh(self, *args):
		self.clear()

		try:
			boot = run_efibootmgr()
		except subprocess.CalledProcessError as e:
			logging.exception("Error running efibootmgr. Please check that it is correctly installed.")
			error_dialog(transient_for=self.window, title="efibootmgr utility not installed!",
						message="Please check that the efibootmgr utility is correctly installed, as this program requires its output.\n" + e,
						on_response=lambda *_: sys.exit(-1))
			return
		except UnicodeDecodeError as e:
			logging.exception("Error decoding efibootmgr -v output.")
			error_dialog(transient_for=self.window, title="Error while decoding efibootmgr output.",
						message="Could not decode efiboomgr output.\n" + e, on_response=lambda *_: sys.exit(-2))
			return

		if boot is not None:
			parsed_efi = parse_efibootmgr(boot)
			for entry in parsed_efi['entries']:
				row = self.append()
				self.set_value(row, EFIStore.ROW_CURRENT, entry['num'] == parsed_efi['boot_current'])
				self.set_value(row, EFIStore.ROW_NUM, entry['num'])
				self.set_value(row, EFIStore.ROW_NAME, entry['name'])
				self.set_value(row, EFIStore.ROW_PATH, entry['path'])
				self.set_value(row, EFIStore.ROW_PARAMETERS, entry['parameters'])
				self.set_value(row, EFIStore.ROW_ACTIVE, entry['active'])
				self.set_value(row, EFIStore.ROW_NEXT, entry['num'] == parsed_efi['boot_next'])
			self.boot_order = self.boot_order_initial = parsed_efi['boot_order']
			self.boot_next = self.boot_next_initial = parsed_efi['boot_next']
			self.boot_current = parsed_efi['boot_current']
			self.timeout = self.timeout_initial = parsed_efi['timeout']
			self.window.timeout_spin.set_value(self.timeout)
			self.reorder()

	def change_boot_next(self, widget, path):
		selected_path = Gtk.TreePath(path)
		for row in self:
			if row.path == selected_path:
				row[EFIStore.ROW_NEXT] = not row[EFIStore.ROW_NEXT]
				self.boot_next = row[EFIStore.ROW_NUM] if row[EFIStore.ROW_NEXT] else None
			else:
				row[EFIStore.ROW_NEXT] = False

	def change_active(self, widget, path):
		selected_path = Gtk.TreePath(path)
		for row in self:
			if row.path == selected_path:
				row[EFIStore.ROW_ACTIVE] = not row[EFIStore.ROW_ACTIVE]
				num = row[EFIStore.ROW_NUM]
				if row[EFIStore.ROW_ACTIVE]:
					if num in self.boot_inactive:
						self.boot_inactive.remove(num)
					else:
						self.boot_active.append(num)
				else:
					if num in self.boot_active:
						self.boot_active.remove(num)
					else:
						self.boot_inactive.append(num)

	def change_timeout(self, timeout_spin):
		self.timeout = timeout_spin.get_value_as_int()

	def fill_row(self, row, current, num, name, path, parameters, active, next):
		self.set_value(row, EFIStore.ROW_CURRENT, current)
		self.set_value(row, EFIStore.ROW_NUM, num)
		self.set_value(row, EFIStore.ROW_NAME, name)
		self.set_value(row, EFIStore.ROW_PATH, path)
		self.set_value(row, EFIStore.ROW_PARAMETERS, parameters)
		self.set_value(row, EFIStore.ROW_ACTIVE, active)
		self.set_value(row, EFIStore.ROW_NEXT, next)

	def add(self, label, path, parameters):
		new_num = "NEW{:d}".format(len(self.boot_add))
		row = self.insert(0)
		self.fill_row(row, False, new_num, label, path, parameters, True, False)
		self.boot_add.append((new_num, label, path, parameters))

	def remove(self, row_index, row_iter):
		num = self.get_value(row_iter, EFIStore.ROW_NUM)
		if num.startswith('NEW'):
			self.boot_add = [entry for entry in self.boot_add if entry[0] != num]
		else:
			for row in self:
				if row[EFIStore.ROW_NUM] == num:
					self.boot_remove.append(num)
					self.boot_order.remove(num)
		super().remove(row_iter)

	def apply_changes(self):
		logging.info("Running command `pkexec sh -c %s`", str(self))
		try:
			subprocess.run(["pkexec", "sh", "-c", str(self)], check=True, capture_output=True)
			self.refresh()
		except subprocess.CalledProcessError as e:
			error_dialog(self.window, f"{e}\n{e.stderr.decode()}", "Error", lambda d, r: d.close())

	def pending_changes(self):
		return (self.boot_next_initial != self.boot_next or
				self.boot_order_initial != self.boot_order or self.boot_add or
				self.boot_remove or self.boot_active or self.boot_inactive
				or self.timeout != self.timeout_initial
			)

	def __str__(self):
		esp = self.esp
		str = ''
		for entry in self.boot_remove:
			str += f'efibootmgr {esp} --delete-bootnum --bootnum {entry}\n'
		for _, label, loader, params in self.boot_add:
			str += f'efibootmgr {esp} --create --label \'{label}\' --loader \'{loader}\' --unicode \'{params}\'\n'
		if self.boot_order != self.boot_order_initial:
			str += f'efibootmgr {esp} --bootorder {",".join(self.boot_order)}\n'
		if self.boot_next_initial != self.boot_next:
			if self.boot_next is None:
				str += f'efibootmgr {esp} --delete-bootnext\n'
			else:
				str += f'efibootmgr {esp} --bootnext {self.boot_next}\n'
		for entry in self.boot_active:
			str += f'efibootmgr {esp} --bootnum {entry} --active\n'
		for entry in self.boot_inactive:
			str += f'efibootmgr {esp} --bootnum {entry} --inactive\n'
		if self.timeout != self.timeout_initial:
			str += f'efibootmgr {esp} --timeout {self.timeout}\n'
		return str


class EFIWindow(Gtk.ApplicationWindow):
	def __init__(self, app, esp):
		Gtk.Window.__init__(self, title="EFI boot manager", application=app)

		vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=10, margin_start=10, margin_bottom=10, margin_end=10)
		self.set_child(vbox)

		self.store = EFIStore(self, esp)
		self.tree = Gtk.TreeView(model=self.store, vexpand=True)
		vbox.append(self.tree)

		renderer_text = Gtk.CellRendererText()
		renderer_check = Gtk.CellRendererToggle(radio=False)
		renderer_radio = Gtk.CellRendererToggle(radio=True)
		renderer_boot_current = Gtk.CellRendererToggle(radio=True, activatable=False)
		renderer_check.connect("toggled", self.store.change_active)
		renderer_radio.connect("toggled", self.store.change_boot_next)
		self.tree.append_column(Gtk.TreeViewColumn("BootCurrent", renderer_boot_current, active=0))
		self.tree.append_column(Gtk.TreeViewColumn("BootNum", renderer_text, text=1))
		self.tree.append_column(Gtk.TreeViewColumn("Name", renderer_text, text=2))
		self.tree.append_column(Gtk.TreeViewColumn("Path", renderer_text, text=3))
		self.tree.append_column(Gtk.TreeViewColumn("Parameters", Gtk.CellRendererText(ellipsize=True), text=4))
		self.tree.append_column(Gtk.TreeViewColumn("Active", renderer_check, active=5))
		self.tree.append_column(Gtk.TreeViewColumn("NextBoot", renderer_radio, active=6))
		for column in self.tree.get_columns():
			column.set_resizable(True)
			column.set_min_width(75)

		hb = Gtk.HeaderBar()
		self.set_titlebar(hb)

		clear_btn = btn_with_icon("edit-clear-all-symbolic")
		clear_btn.set_tooltip_text("clear all")
		clear_btn.connect("clicked", self.on_clicked_discard_changes)
		hb.pack_end(clear_btn)

		write_btn = btn_with_icon("document-save-symbolic")
		write_btn.connect("clicked", self.apply_changes)
		write_btn.set_tooltip_text("save")
		hb.pack_end(write_btn)

		hbox = Gtk.Box(hexpand=True)
		hbox.add_css_class("linked")
		vbox.append(hbox)

		up = btn_with_icon("go-up-symbolic")
		down = btn_with_icon("go-down-symbolic")
		new = btn_with_icon("list-add-symbolic")
		delete = btn_with_icon("list-remove-symbolic")

		up.set_tooltip_text("move up")
		down.set_tooltip_text("move down")
		new.set_tooltip_text("create new entry")
		delete.set_tooltip_text("delete entry")

		hbox.append(up)
		hbox.append(down)
		hbox.append(new)
		hbox.append(delete)

		up.connect("clicked", self.up)
		down.connect("clicked", self.down)
		new.connect("clicked", self.new)
		delete.connect("clicked", self.delete)

		tbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
		tbox.append(Gtk.Label(label="Boot manager timeout in seconds:"))
		self.timeout_spin = Gtk.SpinButton.new_with_range(0, 999, 1)
		self.timeout_spin.connect('value_changed', self.store.change_timeout)
		tbox.append(self.timeout_spin)
		vbox.append(tbox)

		self.connect("close-request", self.on_close_request)
		self.set_default_size(300, 260)
		self.store.refresh()

	def up(self, *args):
		_, selection = self.tree.get_selection().get_selected()
		if not selection == None:
			next = self.store.iter_previous(selection)
			if next:
				self.store.swap(selection, next)

	def down(self, *args):
		_, selection = self.tree.get_selection().get_selected()
		if selection is not None:
			next = self.store.iter_next(selection)
			if next:
				self.store.swap(selection, next)

	def new(self, *args):
		dialog = Gtk.MessageDialog(transient_for=self, modal=True,
				destroy_with_parent=True, message_type=Gtk.MessageType.QUESTION,
				buttons=Gtk.ButtonsType.OK_CANCEL, text="Label is mandatory. It is the name that will show up in your EFI boot menu.\n\n"
				"Path is the path to the loader relative to the ESP, like \\EFI\\Boot\\bootx64.efi\n\n"
				"Parameters is an optional list of aguments to pass to the loader (your kernel parameters if you use EFISTUB)")

		dialog.set_title("New EFI loader")
		yes_button = dialog.get_widget_for_response(Gtk.ResponseType.OK)
		yes_button.set_sensitive(False)
		dialog_box = dialog.get_content_area()

		fields = ["label", "path", "parameters"]
		entries = {}
		grid = Gtk.Grid(row_spacing=2, column_spacing=8, halign=Gtk.Align.CENTER)
		for i, field in enumerate(fields):
			entries[field] = Gtk.Entry()
			entries[field].set_size_request(400, 0)
			label = Gtk.Label(label=field.capitalize() + ":")
			grid.attach(label, 0, i, 1, 1)
			grid.attach(entries[field], 1, i, 1, 1)
		
		dialog_box.append(grid)
		entries["label"].connect('changed', lambda l: yes_button.set_sensitive(l.get_text() != ''))
		def on_response(dialog, response):
			label, path, parameters = map(lambda field: entries[field].get_text(), fields)
			if response == Gtk.ResponseType.OK:
				self.store.add(label, path, parameters)
			dialog.close()
		dialog.connect('response', on_response)
		dialog.show()


	def delete(self, *args):
		_, selection = self.tree.get_selection().get_selected()
		index = self.tree.get_selection().get_selected_rows()[1][0].get_indices()[0]
		if selection is not None:
			self.store.remove(index, selection)

	def apply_changes(self, *args):
		if self.store.pending_changes():
			def on_response(dialog, response):
				if response == Gtk.ResponseType.YES:
					self.store.apply_changes()
				dialog.close()
			dialog = yes_no_dialog(self, "Are you sure you want to continue?",
							"Your changes are about to be written to EFI NVRAM.\nThe following commands will be run:\n" + str(self.store),
							on_response)

	def discard_warning(self, on_response, win):
		if self.store.pending_changes():
			return yes_no_dialog(self, "Are you sure you want to discard?",
									"Your changes will be lost if you don't save them.",
									on_response)
		else:
			return None

	def on_clicked_discard_changes(self, button):
		def on_response(dialog, response):
			if response == Gtk.ResponseType.YES:
				self.store.refresh()
			dialog.close()
		if not self.discard_warning(on_response, self):
			self.store.refresh()


	def on_close_request(self, win):
		def on_response(dialog, response):
			dialog.close()
			if response == Gtk.ResponseType.YES:
				win.destroy()
		if self.discard_warning(on_response, win):
			return True


def run(disk, part):
	if not (disk and part):
		disk, part = auto_detect_esp()

	def on_activate(app):
		win = EFIWindow(app, f"--disk {disk} --part {part}")
		win.show()

	app = Gtk.Application()
	app.connect('activate', on_activate)
	app.run()
