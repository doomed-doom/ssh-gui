use anyhow::{anyhow, Result};
use russh::{client, Disconnect};
use russh::client::{Config, Handle};
use russh::keys::{HashAlg, PrivateKey, PrivateKeyWithHashAlg};
use russh_sftp::client::SftpSession;
use russh_sftp::protocol::OpenFlags;
use serde::{Deserialize, Serialize};
use std::sync::Arc;
use tokio::io::{AsyncBufReadExt, BufReader};
use tokio::fs::File;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use std::path::Path;

#[tokio::main]
async fn main() -> Result<()> {
    env_logger::init();
    process_input().await
}

async fn process_input() -> Result<()> {
    let stdin = tokio::io::stdin();
    let reader = BufReader::new(stdin);
    let mut lines = reader.lines();
    let mut session: Option<Session> = None;

    while let Some(line) = lines.next_line().await? {
        let cmd: Result<Command, _> = serde_json::from_str(&line);
        let response = match cmd {
            Ok(command) => handle_command(&mut session, command).await,
            Err(e) => Response::Error { message: e.to_string() },
        };
        println!("{}", serde_json::to_string(&response)?);
    }
    Ok(())
}

#[derive(Deserialize)]
#[serde(tag = "cmd")]
enum Command {
    Connect {
        host: String,
        port: u16,
        username: String,
        password: Option<String>,
        private_key: Option<String>,
    },
    Exec { command: String },
    SftpList { path: String },
    SftpRemove { path: String },
    SftpMkdir { path: String },
    SftpRmdir { path: String },
    GetHomeDir,
    SftpDownload { remote: String, local: String },
    SftpUpload { local: String, remote: String },
    Disconnect,
}

#[derive(Serialize)]
#[serde(tag = "status")]
enum Response {
    #[serde(rename = "connected")]
    Connected,
    #[serde(rename = "disconnected")]
    Disconnected,
    #[serde(rename = "output")]
    Output { output: String },
    #[serde(rename = "files")]
    Files { files: Vec<FileEntry> },
    #[serde(rename = "home_dir")]
    HomeDir { path: String },
    #[serde(rename = "ok")]
    Ok,
    #[serde(rename = "error")]
    Error { message: String },
}

#[derive(Serialize)]
struct FileEntry {
    name: String,
    is_dir: bool,
    size: u64,
}

async fn handle_command(session: &mut Option<Session>, cmd: Command) -> Response {
    match cmd {
        Command::Connect { host, port, username, password, private_key } => {
            match Session::connect(host, port, username, password, private_key).await {
                Ok(sess) => {
                    *session = Some(sess);
                    Response::Connected
                }
                Err(e) => Response::Error { message: e.to_string() },
            }
        }
        Command::Exec { command } => match session {
            Some(sess) => match sess.exec(&command).await {
                Ok(output) => Response::Output { output },
                Err(e) => Response::Error { message: e.to_string() },
            },
            None => Response::Error { message: "Not connected".into() },
        },
        Command::SftpList { path } => match session {
            Some(sess) => match sess.sftp_list(&path).await {
                Ok(files) => Response::Files { files },
                Err(e) => Response::Error { message: e.to_string() },
            },
            None => Response::Error { message: "Not connected".into() },
        },
        Command::SftpRemove { path } => match session {
            Some(sess) => match sess.sftp_remove(&path).await {
                Ok(_) => Response::Ok,
                Err(e) => Response::Error { message: e.to_string() },
            },
            None => Response::Error { message: "Not connected".into() },
        },
        Command::SftpMkdir { path } => match session {
            Some(sess) => match sess.sftp_mkdir(&path).await {
                Ok(_) => Response::Ok,
                Err(e) => Response::Error { message: e.to_string() },
            },
            None => Response::Error { message: "Not connected".into() },
        },
        Command::SftpRmdir { path } => match session {
            Some(sess) => match sess.sftp_rmdir(&path).await {
                Ok(_) => Response::Ok,
                Err(e) => Response::Error { message: e.to_string() },
            },
            None => Response::Error { message: "Not connected".into() },
        },
        Command::GetHomeDir => match session {
            Some(sess) => match sess.get_home_dir().await {
                Ok(path) => Response::HomeDir { path },
                Err(e) => Response::Error { message: e.to_string() },
            },
            None => Response::Error { message: "Not connected".into() },
        },
        Command::SftpDownload { remote, local } => match session {
            Some(sess) => match sess.sftp_download(&remote, &local).await {
                Ok(_) => Response::Ok,
                Err(e) => Response::Error { message: e.to_string() },
            },
            None => Response::Error { message: "Not connected".into() },
        },
        Command::SftpUpload { local, remote } => match session {
            Some(sess) => match sess.sftp_upload(&local, &remote).await {
                Ok(_) => Response::Ok,
                Err(e) => Response::Error { message: e.to_string() },
            },
            None => Response::Error { message: "Not connected".into() },
        },
        Command::Disconnect => {
            *session = None;
            Response::Disconnected
        }
    }
}

struct Session {
    handle: Handle<Client>,
    sftp: SftpSession,
}

impl Session {
    async fn connect(
        host: String,
        port: u16,
        username: String,
        password: Option<String>,
        private_key: Option<String>,
    ) -> Result<Self> {
        let config = Arc::new(Config::default());
        let mut handle = client::connect(config, (host.as_str(), port), Client {}).await?;

        if let Some(key_str) = private_key {
            let key = PrivateKey::from_openssh(&key_str)?;
            let wrapped = PrivateKeyWithHashAlg::new(Arc::new(key), Some(HashAlg::Sha256));
            handle.authenticate_publickey(username, wrapped).await?;
        } else if let Some(pass) = password {
            handle.authenticate_password(username, pass).await?;
        } else {
            return Err(anyhow!("Missing auth method"));
        }

        let channel = handle.channel_open_session().await?;
        channel.request_subsystem(true, "sftp").await?;
        let sftp = SftpSession::new(channel.into_stream()).await?;

        Ok(Self { handle, sftp })
    }

    async fn exec(&mut self, cmd: &str) -> Result<String> {
        let channel = self.handle.channel_open_session().await?;
        channel.exec(true, cmd).await?;

        let mut output = Vec::new();
        let mut reader = channel.into_stream();
        tokio::io::copy(&mut reader, &mut output).await?;

        Ok(String::from_utf8_lossy(&output).to_string())
    }

    async fn sftp_list(&mut self, path: &str) -> Result<Vec<FileEntry>> {
        let entries = self.sftp.read_dir(path).await?;
        let mut files = Vec::new();
    
        for entry in entries {
            let name = entry.file_name();
            let meta = entry.metadata(); 
    
            files.push(FileEntry {
                name,
                is_dir: meta.is_dir(),
                size: meta.size.unwrap_or(0),
            });
        }
    
        Ok(files)
    }

    async fn sftp_remove(&mut self, path: &str) -> Result<()> {
        self.sftp.remove_file(path).await.map_err(|e| anyhow!(e))
    }

    async fn sftp_mkdir(&mut self, path: &str) -> Result<()> {
        self.sftp.create_dir(path).await.map_err(|e| anyhow!(e))
    }

    async fn sftp_rmdir(&mut self, path: &str) -> Result<()> {
        self.sftp.remove_dir(path).await.map_err(|e| anyhow!(e))
    }

    pub async fn get_home_dir(&mut self) -> Result<String> {
        let output = self.exec("echo $HOME").await?;
        Ok(output.trim().to_string())
    }

    // Загрузка файла с сервера
    pub async fn sftp_download(&mut self, remote: &str, local: &str) -> Result<()> {
        let mut remote_file = self.sftp.open(remote).await?; // Открытие удаленного файла
        let mut local_file = File::create(local).await?; // Создание локального файла
        let mut buffer = vec![0u8; 8192];

        loop {
            let n = remote_file.read(&mut buffer).await?;
            if n == 0 {
                break;
            }
            local_file.write_all(&buffer[..n]).await?;
        }

        Ok(())
    }

    // Выгрузка файла на сервер
    pub async fn sftp_upload(&mut self, local: &str, remote: &str) -> Result<()> {
        // 1. Открываем локальный файл
        let mut local_file = match File::open(local).await {
            Ok(file) => file,
            Err(e) => return Err(anyhow!("Failed to open local file {}: {}", local, e)),
        };
        
        // 2. Создаём все родительские директории
        if let Some(parent) = Path::new(remote).parent() {
            if !parent.exists() {
                tokio::fs::create_dir_all(parent).await.map_err(|e| {
                    anyhow!("Failed to create directories for {}: {}", remote, e)
                })?;
            }
        }
        
        // 3. Создаём новый файл на сервере (режим 0o644 - владелец RW, остальные R)
        let mut remote_file = match self.sftp.create(remote).await {
            Ok(file) => file,
            Err(e) => return Err(anyhow!("Failed to create remote file {}: {}", remote, e)),
        };
        
        // 4. Копируем данные
        let mut buffer = vec![0u8; 8192];
        loop {
            let n = match local_file.read(&mut buffer).await {
                Ok(n) if n == 0 => break,
                Ok(n) => n,
                Err(e) => return Err(anyhow!("Error reading local file: {}", e)),
            };
            
            remote_file.write_all(&buffer[..n]).await.map_err(|e| {
                anyhow!("Error writing to remote file: {}", e)
            })?;
        }
        
        Ok(())
    }
}

struct Client;

impl client::Handler for Client {
    type Error = russh::Error;
    async fn check_server_key(&mut self, _key: &russh::keys::PublicKey) -> Result<bool, Self::Error> {
        Ok(true)
    }
}
