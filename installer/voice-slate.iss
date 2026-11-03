; The Windows installer of Fel's Voice Slate. tools/build_installer.py makes the program folder and runs this with
; /DAppVersion and /DAppFolder. It installs for the person running it, so it asks for no administrator rights, and the program
; folder stays writable, which the transcript setup needs for its own environment and speech model.
; Recordings are not in the program folder, so neither an update nor uninstalling touches them.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef AppFolder
  #define AppFolder "..\build\app"
#endif
#define AppName "Fel's Voice Slate"
#define Python "{app}\python\pythonw.exe"
; -E and -s: a PYTHONPATH or a user's own packages from another Python must not reach this one
#define Start "-E -s -m recorder"

[Setup]
AppId={{6E7C2B7B-3F0A-4D2E-9C55-5A1E2D7B9F10}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Fel101
AppPublisherURL=https://github.com/Fel101/fels-voice-slate
AppSupportURL=https://github.com/Fel101/fels-voice-slate/issues
DefaultDirName={localappdata}\Programs\Fels Voice Slate
DisableDirPage=auto
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputBaseFilename=Fels-Voice-Slate-{#AppVersion}-Setup
SetupIconFile=..\recorder\resources\voice-slate.ico
UninstallDisplayIcon={app}\recorder\resources\voice-slate.ico
UninstallDisplayName={#AppName}
LicenseFile=..\LICENSE
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; The program holds this while it runs, so the installer can ask for it to be closed first
AppMutex=Local\FelsVoiceSlate.App.SingleInstance
CloseApplications=no

[Messages]
ConfirmUninstall=Remove %1 from this computer?%n%nYour recordings and settings are not in the program folder, and they stay where they are.

[Tasks]
Name: "desktopicon"; Description: "Put an icon on the desktop"; Flags: unchecked

[InstallDelete]
; An update replaces these whole, so nothing of the version before is left among the new files
Type: filesandordirs; Name: "{app}\python"
Type: filesandordirs; Name: "{app}\recorder"

[Files]
Source: "{#AppFolder}\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion createallsubdirs

[Icons]
Name: "{userprograms}\{#AppName}"; Filename: "{#Python}"; Parameters: "{#Start}"; WorkingDir: "{app}"; IconFilename: "{app}\recorder\resources\voice-slate.ico"; AppUserModelID: "FelsVoiceSlate.App"
Name: "{userdesktop}\{#AppName}"; Filename: "{#Python}"; Parameters: "{#Start}"; WorkingDir: "{app}"; IconFilename: "{app}\recorder\resources\voice-slate.ico"; AppUserModelID: "FelsVoiceSlate.App"; Tasks: desktopicon

[Run]
Filename: "{#Python}"; Parameters: "{#Start}"; WorkingDir: "{app}"; Description: "Start {#AppName}"; Flags: postinstall nowait skipifsilent

[UninstallDelete]
; What the program folder gained after it was installed: compiled files, and the transcript setup's recognizer and speech model
Type: filesandordirs; Name: "{app}\python"
Type: filesandordirs; Name: "{app}\recorder"
Type: filesandordirs; Name: "{app}\.venv-asr"
Type: filesandordirs; Name: "{app}\models"
