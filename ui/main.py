import sys
import json
import os
from pathlib import Path
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QTabWidget, QVBoxLayout, QHBoxLayout,
                             QFileSystemModel, QTreeView, QActionGroup, QSplitter, QTextEdit, QTabBar, QPushButton,
                             QDialog, QLabel, QLineEdit, QDialogButtonBox, QFormLayout, QMessageBox,
                             QMenu, QAction, QSpinBox, QComboBox, QTreeWidget, QTreeWidgetItem, QHeaderView,
                             QFileIconProvider, QStyle, QFileDialog)
from PyQt5.QtCore import QDir, Qt, QProcess, QTextStream, QIODevice, QTimer, QSettings, QFileInfo, QMimeData, QUrl
from PyQt5.QtGui import QTextCursor, QTextCharFormat, QColor, QIcon, QFont, QPalette, QKeyEvent, QDragEnterEvent, QDropEvent, QDragMoveEvent

class TerminalWidget(QTextEdit):
    def __init__(self, parent=None, browser_tab=None):
        super().__init__(parent)
        self.browser_tab = browser_tab
        self.setReadOnly(False)
        self.setAcceptRichText(False)
        self.setLineWrapMode(QTextEdit.NoWrap)
        
        font = QFont("Monospace")
        font.setStyleHint(QFont.TypeWriter)
        self.setFont(font)
        
        palette = QPalette()
        palette.setColor(QPalette.Base, Qt.black)
        palette.setColor(QPalette.Text, Qt.white)
        self.setPalette(palette)
        
        self.history = []
        self.history_index = -1
        self.current_prompt = ""
        self.pending_output = ""
        
        self.init_prompt()

    def get_prompt(self):
        username = self.browser_tab.connection_data.get("username", "user")
        host = self.browser_tab.connection_data.get("host", "host")
        path = self.browser_tab.current_path or "~"
        if self.browser_tab.home_dir and path.startswith(self.browser_tab.home_dir):
            path = "~" + path[len(self.browser_tab.home_dir):]
        self.current_prompt = f"{username}@{host}:{path}$ "
        return self.current_prompt

    def init_prompt(self):
        self.moveCursor(QTextCursor.End)
        self.insertPlainText(self.get_prompt())
        self.moveCursor(QTextCursor.End)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.execute_current_command()
        elif event.key() == Qt.Key_Up:
            self.navigate_history(-1)
        elif event.key() == Qt.Key_Down:
            self.navigate_history(1)
        elif event.key() in (Qt.Key_Backspace, Qt.Key_Left):
            cursor = self.textCursor()
            if cursor.positionInBlock() > len(self.current_prompt):
                super().keyPressEvent(event)
        elif event.key() == Qt.Key_Home:
            cursor = self.textCursor()
            cursor.movePosition(QTextCursor.StartOfBlock)
            cursor.movePosition(QTextCursor.Right, QTextCursor.MoveAnchor, len(self.current_prompt))
            self.setTextCursor(cursor)
        else:
            super().keyPressEvent(event)

    def execute_current_command(self):
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.StartOfBlock)
        cursor.movePosition(QTextCursor.EndOfBlock, QTextCursor.KeepAnchor)
        command = cursor.selectedText()[len(self.current_prompt):].strip()
        
        if not command:
            self.moveCursor(QTextCursor.End)
            self.insertPlainText("\n" + self.get_prompt())
            self.moveCursor(QTextCursor.End)
            return
            
        self.history.append(command)
        self.history_index = len(self.history)
        
        # Выводим саму команду
        self.moveCursor(QTextCursor.End)
        self.insertPlainText("\n")
        
        if command.startswith("cd "):
            path = command[3:].strip()
            if path == "~":
                path = self.browser_tab.home_dir
            elif path == ".":
                path = self.browser_tab.current_path
            elif path == "..":
                path = os.path.dirname(self.browser_tab.current_path.rstrip('/'))
            self.browser_tab.send_command({"cmd": "SftpList", "path": path})
        elif command == "ls":
            self.browser_tab.send_command({"cmd": "SftpList", "path": self.browser_tab.current_path or "."})
        elif command == "pwd":
            self.append_output(self.browser_tab.current_path or "~")
        elif command == "disconnect":
            self.browser_tab.disconnect()
        else:
            self.browser_tab.send_command({"cmd": "Exec", "command": command})

    def navigate_history(self, direction):
        if not self.history:
            return
            
        self.history_index = max(0, min(self.history_index + direction, len(self.history) - 1))
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.StartOfBlock)
        cursor.movePosition(QTextCursor.EndOfBlock, QTextCursor.KeepAnchor)
        cursor.removeSelectedText()
        cursor.insertText(self.current_prompt + self.history[self.history_index])
        self.setTextCursor(cursor)

    def append_output(self, text):
        # Schedule output processing to avoid race conditions
        self.pending_output += text
        QTimer.singleShot(0, self._process_output)

    def _process_output(self):
        if not self.pending_output:
            return
            
        self.moveCursor(QTextCursor.End)
        
        # Remove any trailing newlines from output
        output = self.pending_output.rstrip('\n')
        self.pending_output = ""
        
        # If we're not at start of line, add newline first
        cursor = self.textCursor()
        if cursor.positionInBlock() != 0:
            self.insertPlainText("\n")
        
        # Insert the actual output
        self.insertPlainText(output)
        
        # Add new prompt on new line
        self.insertPlainText("\n" + self.get_prompt())
        self.moveCursor(QTextCursor.End)


class UnifiedFileSystemView(QTreeWidget):
    def __init__(self, parent=None, is_remote=False):
        super().__init__(parent)
        self.is_remote = is_remote
        self.parent_browser = parent
        self.setHeaderLabels(["Name", "Size", "Type", "Modified"])
        self.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.header().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.header().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.setRootIsDecorated(True)
        self.setSortingEnabled(True)
        self.itemDoubleClicked.connect(self.on_item_double_clicked)
        
        # Enable drag and drop for remote file view
        if self.is_remote:
            self.setAcceptDrops(True)
            self.setDragEnabled(False)
            self.setDragDropMode(QTreeWidget.DropOnly)
        else:
            self.setDragEnabled(True)
            self.setAcceptDrops(False)
            self.setDragDropMode(QTreeWidget.DragOnly)
        
        self.icon_provider = QFileIconProvider()
        self.folder_icon = self.icon_provider.icon(QFileIconProvider.Folder)
        self.file_icon = self.icon_provider.icon(QFileIconProvider.File)
        
        # Enable context menu
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self.show_context_menu)
    
    def update_files(self, files):
        self.clear()
        
        if self.is_remote:
            self.update_remote_files(files)
        else:
            self.update_local_files(files)
    
    def update_remote_files(self, files):
        self.clear()
        
        # Добавляем кнопку ".." только если текущий путь не корневой
        if self.parent_browser.current_path and self.parent_browser.current_path != "/":
            parent_item = QTreeWidgetItem()
            parent_item.setText(0, "..")
            parent_item.setIcon(0, self.folder_icon)
            parent_item.setData(0, Qt.UserRole, {"is_dir": True, "name": ".."})
            self.addTopLevelItem(parent_item)

        for file_info in files:
            item = QTreeWidgetItem()
            item.setText(0, file_info.get("name", ""))
            
            size = file_info.get("size", 0)
            item.setText(1, self.format_size(size) if not file_info.get("is_dir", False) else "")
            
            item_type = "Directory" if file_info.get("is_dir", False) else "File"
            item.setText(2, item_type)
            
            modified = file_info.get("modified", "")
            item.setText(3, modified)
            
            item.setIcon(0, self.folder_icon if file_info.get("is_dir", False) else self.file_icon)
            item.setData(0, Qt.UserRole, file_info)
            self.addTopLevelItem(item)
    
    def update_local_files(self, path):
        self.clear()
        
        if QDir(path).dirName():
            parent_item = QTreeWidgetItem()
            parent_item.setText(0, "..")
            parent_item.setIcon(0, self.folder_icon)
            parent_item.setData(0, Qt.UserRole, {"is_dir": True, "path": str(Path(path).parent)})
            self.addTopLevelItem(parent_item)
        
        dir_info = QFileInfo(path)
        if dir_info.isDir():
            dir = QDir(path)
            for file_info in dir.entryInfoList(QDir.AllEntries | QDir.NoDotAndDotDot | QDir.Hidden, 
                                            QDir.DirsFirst | QDir.IgnoreCase):
                item = QTreeWidgetItem()
                item.setText(0, file_info.fileName())
                
                if file_info.isDir():
                    item.setText(1, "")
                    item.setText(2, "Directory")
                else:
                    item.setText(1, self.format_size(file_info.size()))
                    item.setText(2, "File")
                
                item.setText(3, file_info.lastModified().toString("yyyy-MM-dd HH:mm:ss"))
                item.setIcon(0, self.folder_icon if file_info.isDir() else self.file_icon)
                item.setData(0, Qt.UserRole, {
                    "is_dir": file_info.isDir(),
                    "path": file_info.absoluteFilePath()
                })
                self.addTopLevelItem(item)
    
    def format_size(self, size):
        if size < 1024:
            return f"{size} B"
        elif size < 1024*1024:
            return f"{size/1024:.1f} KB"
        elif size < 1024*1024*1024:
            return f"{size/(1024*1024):.1f} MB"
        else:
            return f"{size/(1024*1024*1024):.1f} GB"
    
    def on_item_double_clicked(self, item):
        file_info = item.data(0, Qt.UserRole)
        
        if file_info.get("is_dir", False):
            if self.is_remote:
                if file_info["name"] == "..":
                    # Поднимаемся на уровень выше
                    parent_path = os.path.dirname(self.parent_browser.current_path.rstrip('/'))
                    # Не поднимаемся выше корня
                    if parent_path == self.parent_browser.current_path:
                        return
                    path = parent_path
                else:
                    # Переходим в выбранную папку
                    path = os.path.join(self.parent_browser.current_path, file_info["name"])
                
                # Обновляем текущий путь и запрашиваем содержимое
                self.parent_browser.current_path = path
                self.parent_browser.send_command({"cmd": "SftpList", "path": path})
            else:
                # Локальная файловая система
                self.update_local_files(file_info["path"])
    
    def show_context_menu(self, position):
        item = self.itemAt(position)
        if not item:
            return
            
        file_info = item.data(0, Qt.UserRole)
        menu = QMenu()
        
        if self.is_remote:
            # Context menu for remote files
            if not file_info.get("is_dir", False):
                download_action = QAction("Download", self)
                download_action.triggered.connect(lambda: self.download_file(file_info))
                menu.addAction(download_action)
            
            delete_action = QAction("Delete", self)
            delete_action.triggered.connect(lambda: self.delete_file(file_info))
            menu.addAction(delete_action)
        else:
            # Context menu for local files
            if not file_info.get("is_dir", False):
                upload_action = QAction("Upload to Server", self)
                upload_action.triggered.connect(lambda: self.upload_file(file_info))
                menu.addAction(upload_action)
        
        menu.exec_(self.viewport().mapToGlobal(position))
    
    def download_file(self, file_info):
        # Ask where to save the file
        save_path, _ = QFileDialog.getSaveFileName(
            self, 
            "Save File", 
            os.path.join(QDir.homePath(), file_info["name"]),
            "All Files (*)"
        )
        
        if not save_path:
            return
            
        # Send download command to server
        remote_path = os.path.join(self.parent_browser.current_path, file_info["name"])
        self.parent_browser.send_command({
            "cmd": "SftpDownload",
            "remote": remote_path,
            "local": save_path
        })
        self.parent_browser.terminal.append_output(f"Downloading {remote_path} to {save_path}...")
    
    def delete_file(self, file_info):
        reply = QMessageBox.question(
            self, 
            "Confirm Delete", 
            f"Are you sure you want to delete {file_info['name']}?",
            QMessageBox.Yes | QMessageBox.No
        )
        
        if reply == QMessageBox.Yes:
            remote_path = os.path.join(self.parent_browser.current_path, file_info["name"])
            self.parent_browser.send_command({
                "cmd": "SftpDelete",
                "path": remote_path,
                "is_dir": file_info.get("is_dir", False)
            })
            self.parent_browser.terminal.append_output(f"Deleting {remote_path}...")
    
    def upload_file(self, file_info):
        if not self.parent_browser.connected:
            QMessageBox.warning(self, "Error", "Not connected to server")
            return
        
        # Получаем только имя файла
        filename = os.path.basename(file_info["path"])
        
        # Всегда используем относительный путь (имя файла)
        remote_filename = filename
        
        self.parent_browser.send_command({
            "cmd": "SftpUpload",
            "local": file_info["path"],  # Полный локальный путь
            "remote": remote_filename    # Только имя файла
        })
        
        # Понятное сообщение для пользователя
        msg = f"Uploading {file_info['path']} to {self.parent_browser.current_path}/{filename}"
        self.parent_browser.terminal.append_output(msg)

    # Drag and drop implementation
    def dragEnterEvent(self, event: QDragEnterEvent):
        if self.is_remote and event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()
    
    def dragMoveEvent(self, event: QDragMoveEvent):
        if self.is_remote and event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()
    
    def dropEvent(self, event: QDropEvent):
        if not self.is_remote or not self.parent_browser.connected:
            event.ignore()
            return
            
        mime_data = event.mimeData()
        if mime_data.hasUrls():
            for url in mime_data.urls():
                local_path = url.toLocalFile()
                if os.path.isfile(local_path):
                    remote_path = os.path.join(
                        self.parent_browser.current_path,
                        os.path.basename(local_path))
                    
                    self.parent_browser.send_command({
                        "cmd": "SftpUpload",
                        "local": local_path,
                        "remote": remote_path
                    })
                    self.parent_browser.terminal.append_output(f"Uploading {local_path} to {remote_path}...")
            
            event.acceptProposedAction()
        else:
            event.ignore()


class ConnectionDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("SSH Connection Settings")
        
        self.host = QLineEdit("localhost")
        self.port = QLineEdit("22")
        self.username = QLineEdit()
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        self.password.setPlaceholderText("")
        
        self.key_path = QLineEdit()
        self.key_path.setPlaceholderText("Path to SSH key")
        
        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        
        layout = QFormLayout(self)
        layout.addRow("Host:", self.host)
        layout.addRow("Port:", self.port)
        layout.addRow("Username:", self.username)
        layout.addRow("Password:", self.password)
        layout.addRow("SSH Key:", self.key_path)
        layout.addRow(button_box)


class BrowserTab(QWidget):
    def __init__(self, connection_data=None, parent=None):
        super().__init__(parent)
        self.connection_data = connection_data or {}
        self.process = QProcess()
        self.process.setProcessChannelMode(QProcess.MergedChannels)
        self.process.readyReadStandardOutput.connect(self.handle_output)
        self.process.readyReadStandardError.connect(self.handle_error)
        self.process.finished.connect(self.on_process_finished)
        self.connected = False
        self.current_path = None  # Будет установлено после подключения
        self.home_dir = None      # Домашняя директория на сервере
        
        self.setup_ui()
        self.connect_to_host()
    
    def setup_ui(self):
        main_layout = QHBoxLayout(self)
        splitter = QSplitter()
        
        self.terminal = TerminalWidget(browser_tab=self)
        
        right_splitter = QSplitter(Qt.Vertical)
        
        self.local_file_view = UnifiedFileSystemView(self, is_remote=False)
        self.local_file_view.update_files(QDir.homePath())
        
        self.remote_file_view = UnifiedFileSystemView(self, is_remote=True)
        
        right_splitter.addWidget(self.local_file_view)
        right_splitter.addWidget(self.remote_file_view)
        
        splitter.addWidget(self.terminal)
        splitter.addWidget(right_splitter)
        
        splitter.setSizes([self.width() // 2, self.width() // 2])
        right_splitter.setSizes([self.height() // 2, self.height() // 2])
        
        main_layout.addWidget(splitter)
    
    def disconnect(self):
        if self.process.state() == QProcess.Running:
            self.process.write(json.dumps({"cmd":"Disconnect"}).encode() + b'\n')
            self.terminal.append_output("Disconnecting from server...")
    
    def on_process_finished(self, exit_code, exit_status):
        self.terminal.append_output(f"\nConnection closed (code: {exit_code})")
        self.connected = False
    
    def connect_to_host(self):
        backend_path = Path("../target/debug/ssh_backend").absolute()
        
        if not backend_path.exists():
            self.terminal.append_output(f"Error: SSH backend not found at {backend_path}")
            return
        
        self.process.start(str(backend_path))
        
        if not self.process.waitForStarted(5000):
            self.terminal.append_output("Error: Failed to start SSH backend")
            return
        
        connect_cmd = {
            "cmd": "Connect",
            "host": self.connection_data['host'],
            "port": int(self.connection_data['port']),
            "username": self.connection_data['username'],
        }
        
        if self.connection_data.get('password'):
            connect_cmd['password'] = self.connection_data['password']
        if self.connection_data.get('key'):
            connect_cmd['key'] = self.connection_data['key']
        
        json_str = json.dumps(connect_cmd) + "\n"
        self.process.write(json_str.encode())
        self.terminal.append_output(f"Connecting to {self.connection_data['username']}@{self.connection_data['host']}...")
        
        self.connection_timeout = QTimer(self)
        self.connection_timeout.setSingleShot(True)
        self.connection_timeout.timeout.connect(self.check_connection_status)
        self.connection_timeout.start(10000)
    
    def check_connection_status(self):
        if not self.connected:
            self.terminal.append_output("Error: Connection timeout")
            self.process.terminate()
    
    def handle_output(self):
        data = self.process.readAllStandardOutput().data().decode().strip()
        if not data:
            return
        
        try:
            response = json.loads(data)
            if response.get("status") == "connected":
                self.connected = True
                self.connection_timeout.stop()
                self.terminal.append_output("SSH connection established!")
                
                # Запрашиваем домашнюю директорию
                self.send_command({"cmd": "GetHomeDir"})
                
            elif response.get("status") == "home_dir":
                self.home_dir = response.get("path")
                self.current_path = self.home_dir
                self.send_command({"cmd": "SftpList", "path": self.current_path})

            elif response.get("status") == "ok":
                self.terminal.append_output("Всё успешно выполнено")
                
            elif response.get("status") == "files":
                files = response.get("files", [])
                path = response.get("path", ".")
                self.current_path = path if path != "." else self.home_dir  # Исправляем здесь
                
                # Форматируем вывод для команды ls
                if len(files) > 0:
                    file_list = "  ".join([f["name"] + ("/" if f.get("is_dir", False) else "") 
                                        for f in files])
                    self.terminal.append_output(file_list)
                
                self.remote_file_view.update_files(files)
                
            elif response.get("status") == "output":
                self.terminal.append_output(response.get("output", ""))
            elif response.get("status") == "error":
                self.terminal.append_output("Error: " + response.get("message", "Unknown error"))
            elif response.get("status") == "download_complete":
                self.terminal.append_output(f"Download complete: {response.get('local')}")
            elif response.get("status") == "upload_complete":
                self.terminal.append_output(f"Upload complete: {response.get('remote')}")
                # Refresh remote file list
                self.send_command({"cmd": "SftpList", "path": self.current_path})
            elif response.get("status") == "delete_complete":
                self.terminal.append_output(f"Delete complete: {response.get('path')}")
                # Refresh remote file list
                self.send_command({"cmd": "SftpList", "path": self.current_path})
            else:
                self.terminal.append_output(data)
        except json.JSONDecodeError:
            self.terminal.append_output(data)
    
    def handle_error(self):
        error = self.process.readAllStandardError().data().decode()
        if error:
            self.terminal.append_output("Error output:\n" + error)
    
    def send_command(self, command_data):
        if self.process.state() == QProcess.Running:
            json_str = json.dumps(command_data) + "\n"
            self.process.write(json_str.encode())
    
    def closeEvent(self, event):
        self.disconnect()
        if self.process.state() == QProcess.Running:
            self.process.waitForFinished(1000)
        super().closeEvent(event)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SSH Client")
        self.setGeometry(100, 100, 1000, 700)
        
        self.settings = QSettings("config.cfg", QSettings.IniFormat)
        self.load_settings()
        
        self.tab_widget = QTabWidget()
        self.tab_widget.setTabsClosable(True)
        self.tab_widget.tabCloseRequested.connect(self.close_tab)
        
        add_button = QPushButton("  +  ")
        add_button.setFixedWidth(60)
        add_button.clicked.connect(self.add_new_tab)
        
        settings_button = QPushButton("  ≡  ")
        settings_button.setFixedWidth(60)
        settings_button.clicked.connect(self.show_settings_menu)
        
        buttons_container = QWidget()
        buttons_layout = QHBoxLayout()
        buttons_layout.setContentsMargins(0, 0, 0, 0)
        buttons_layout.setSpacing(5)
        buttons_layout.addWidget(add_button)
        buttons_layout.addWidget(settings_button)
        buttons_container.setLayout(buttons_layout)
        
        self.tab_widget.setCornerWidget(buttons_container, Qt.TopRightCorner)
        
        self.setCentralWidget(self.tab_widget)
        
        self.apply_theme()
        
        if not self.add_new_tab():
            self.close()
    
    def load_settings(self):
        self.font_size = self.settings.value("font-size", 12, int)
        self.theme = self.settings.value("theme", "light", str)
    
    def save_settings(self):
        self.settings.setValue("font-size", self.font_size)
        self.settings.setValue("theme", self.theme)
        self.settings.sync()
    
    def apply_theme(self):
        font = QFont("Monospace")
        font.setPointSize(self.font_size)
        QApplication.setFont(font)
        
        if self.theme == "dark":
            self.setStyleSheet("""
                QMainWindow, QDialog, QWidget {
                    background-color: #333;
                    color: #eee;
                }
                QTextEdit, QTreeView, QLineEdit, QSpinBox, QComboBox {
                    background-color: #444;
                    color: #eee;
                    border: 1px solid #555;
                }
                QTabWidget::pane {
                    border: 1px solid #555;
                    background: #333;
                }
                QTabBar::tab {
                    background: #444;
                    color: #eee;
                    padding: 5px;
                    border: 1px solid #555;
                }
                QTabBar::tab:selected {
                    background: #555;
                }
                QPushButton {
                    background: #555;
                    color: #eee;
                    border: 1px solid #666;
                    padding: 8px;
                    min-width: 60px;
                }
                QPushButton:hover {
                    background: #666;
                }
            """)
        else:
            self.setStyleSheet("""
                QPushButton {
                    padding: 8px;
                    min-width: 60px;
                }
            """)
        
        for i in range(self.tab_widget.count()):
            tab = self.tab_widget.widget(i)
            if hasattr(tab, 'terminal'):
                tab.terminal.setPalette(tab.terminal.palette())
    
    def show_settings_menu(self):
        menu = QMenu(self)
        
        font_menu = QMenu("Font Size", self)
        font_group = QActionGroup(self)
        
        for size in range(8, 21):
            action = QAction(str(size), self)
            action.setCheckable(True)
            action.setChecked(size == self.font_size)
            action.triggered.connect(lambda _, s=size: self.set_font_size(s))
            font_group.addAction(action)
            font_menu.addAction(action)
        
        theme_menu = QMenu("Theme", self)
        theme_group = QActionGroup(self)
        
        for theme in ["light", "dark"]:
            action = QAction(theme.capitalize(), self)
            action.setCheckable(True)
            action.setChecked(theme == self.theme)
            action.triggered.connect(lambda _, t=theme: self.set_theme(t))
            theme_group.addAction(action)
            theme_menu.addAction(action)
        
        menu.addMenu(font_menu)
        menu.addMenu(theme_menu)
        
        menu.exec_(self.sender().mapToGlobal(self.sender().rect().bottomLeft()))
    
    def set_font_size(self, size):
        self.font_size = size
        self.save_settings()
        self.apply_theme()
    
    def set_theme(self, theme):
        self.theme = theme
        self.save_settings()
        self.apply_theme()
    
    def add_new_tab(self):
        dialog = ConnectionDialog(self)
        if dialog.exec_() == QDialog.Accepted:
            connection_data = {
                'host': dialog.host.text(),
                'port': dialog.port.text(),
                'username': dialog.username.text(),
            }
            
            if dialog.password.text():
                connection_data['password'] = dialog.password.text()
            if dialog.key_path.text():
                connection_data['key'] = dialog.key_path.text()
            
            if not connection_data['host'] or not connection_data['username']:
                QMessageBox.warning(self, "Error", "Host and Username are required")
                return self.add_new_tab()
            
            try:
                port = int(connection_data['port'])
                if not (0 < port <= 65535):
                    raise ValueError
            except ValueError:
                QMessageBox.warning(self, "Error", "Port must be a number between 1 and 65535")
                return self.add_new_tab()
            
            tab = BrowserTab(connection_data)
            tab_index = self.tab_widget.addTab(
                tab, 
                f"{connection_data['username']}@{connection_data['host']}"
            )
            self.tab_widget.setCurrentIndex(tab_index)
            return True
        return False
    
    def close_tab(self, index):
        widget = self.tab_widget.widget(index)
        if hasattr(widget, 'process') and widget.process.state() == QProcess.Running:
            widget.process.write(json.dumps({"cmd":"Disconnect"}).encode() + b'\n')
            widget.process.waitForFinished(1000)
        
        self.tab_widget.removeTab(index)
        
        if self.tab_widget.count() == 0:
            self.close()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = MainWindow()
    
    if window.tab_widget.count() > 0:
        window.show()
        sys.exit(app.exec_())
    else:
        sys.exit(0)