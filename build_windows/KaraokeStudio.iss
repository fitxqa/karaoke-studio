; Inno Setup script: packs dist\KaraokeStudio (Python + AI tools + ffmpeg + app) into one Setup.exe
#define AppName "Karaoke Studio"
#define AppVer "1.0.0"
#ifndef SrcDir
  #define SrcDir "..\dist\KaraokeStudio"
#endif
#ifndef OutDir
  #define OutDir "..\dist"
#endif

[Setup]
AppId={{6E0D6C1B-3F1A-4C59-9E0B-6A2B7A5C9F11}
AppName={#AppName}
AppVersion={#AppVer}
AppPublisher=FitX
DefaultDirName={localappdata}\Programs\KaraokeStudio
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir={#OutDir}
OutputBaseFilename=KaraokeStudio-Setup
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\KaraokeStudio.exe
UninstallDisplayName={#AppName}
Compression=lzma2/fast
SolidCompression=yes
LZMAUseSeparateProcess=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "{#SrcDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\KaraokeStudio.exe"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\KaraokeStudio.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\KaraokeStudio.exe"; Description: "Start Karaoke Studio now"; Flags: postinstall nowait skipifsilent

[UninstallDelete]
; files added later by the yt-dlp auto-update and Python's cache
Type: filesandordirs; Name: "{app}\python"
Type: filesandordirs; Name: "{app}\server"
