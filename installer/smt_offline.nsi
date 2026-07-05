; ============================================================
;  Smart Money Trader - OFFLINE (self-contained) installer
;  Bundles app + Python runtime + all packages + dashboard.
;  Installs with NO internet connection required.
;  Built by build_installers.bat (runs makensis from this installer\ folder).
;  All paths are relative to this installer\ directory.
; ============================================================

Unicode true
!include "MUI2.nsh"

!define AppName     "Smart Money Trader"
!define AppVersion  "3.0"
!define AppPublisher "Subhash Chand Sharma"
!define AppDir      "SmartMoneyTrader"
!define UninstKey   "Software\Microsoft\Windows\CurrentVersion\Uninstall\SmartMoneyTrader"

Name "${AppName}"
OutFile "..\installer_output\SmartMoneyTrader_Setup_Offline_SelfContained.exe"
InstallDir "$PROGRAMFILES64\${AppDir}"
RequestExecutionLevel admin
SetCompressor /SOLID lzma

!define MUI_ICON "..\SMT_Logo.ico"
!define MUI_UNICON "..\SMT_Logo.ico"
!define MUI_ABORTWARNING

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_COMPONENTS
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_RUN
!define MUI_FINISHPAGE_RUN_TEXT "Run first-time setup now (offline - installs bundled runtime + packages)"
!define MUI_FINISHPAGE_RUN_FUNCTION RunFirstTimeSetup
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"

Function RunFirstTimeSetup
  ExecShell "open" "$INSTDIR\setup_offline.bat"
FunctionEnd

Section "Smart Money Trader (required)" SecMain
  SectionIn RO
  SetShellVarContext all

  ; Backend code + config (NEVER bundle the dev virtualenv)
  SetOutPath "$INSTDIR\backend"
  File /r /x ".venv" /x "__pycache__" /x "*.pyc" "..\backend\*"

  ; Prebuilt dashboard (served by the backend at /app)
  SetOutPath "$INSTDIR\frontend\dist"
  File /r "..\frontend\dist\*"

  ; Bundled Python runtime (embeddable zip, unpacked on first run)
  SetOutPath "$INSTDIR\runtime_src"
  File "/oname=python-embed.zip" "..\build_assets\python-3.11.9-embed-amd64.zip"

  ; Bundled offline packages (pip wheels)
  SetOutPath "$INSTDIR\wheels"
  File /r "..\wheels\*"

  ; User manual
  SetOutPath "$INSTDIR\docs"
  File "..\docs\SMT_User_Manual.pdf"

  ; Scripts + icon (start/stop/setup live in this installer\ folder)
  SetOutPath "$INSTDIR"
  File "start_smt.bat"
  File "stop_smt.bat"
  File "setup_offline.bat"
  File "..\SMT_Logo.ico"

  ; Start Menu shortcuts
  CreateDirectory "$SMPROGRAMS\${AppName}"
  CreateShortCut "$SMPROGRAMS\${AppName}\Start Smart Money Trader.lnk" "$INSTDIR\start_smt.bat" "" "$INSTDIR\SMT_Logo.ico"
  CreateShortCut "$SMPROGRAMS\${AppName}\Stop Smart Money Trader.lnk"  "$INSTDIR\stop_smt.bat"  "" "$INSTDIR\SMT_Logo.ico"
  CreateShortCut "$SMPROGRAMS\${AppName}\First Time Setup.lnk"         "$INSTDIR\setup_offline.bat" "" "$INSTDIR\SMT_Logo.ico"
  CreateShortCut "$SMPROGRAMS\${AppName}\User Manual.lnk"              "$INSTDIR\docs\SMT_User_Manual.pdf"
  CreateShortCut "$SMPROGRAMS\${AppName}\Uninstall ${AppName}.lnk"     "$INSTDIR\uninstall.exe"

  ; Uninstaller + Add/Remove Programs entry
  WriteUninstaller "$INSTDIR\uninstall.exe"
  WriteRegStr   HKLM "${UninstKey}" "DisplayName"     "${AppName}"
  WriteRegStr   HKLM "${UninstKey}" "DisplayVersion"  "${AppVersion}"
  WriteRegStr   HKLM "${UninstKey}" "Publisher"       "${AppPublisher}"
  WriteRegStr   HKLM "${UninstKey}" "DisplayIcon"     "$INSTDIR\SMT_Logo.ico"
  WriteRegStr   HKLM "${UninstKey}" "UninstallString" "$\"$INSTDIR\uninstall.exe$\""
  WriteRegDWORD HKLM "${UninstKey}" "NoModify" 1
  WriteRegDWORD HKLM "${UninstKey}" "NoRepair" 1
SectionEnd

Section "Desktop shortcut" SecDesktop
  SetShellVarContext all
  CreateShortCut "$DESKTOP\Smart Money Trader.lnk" "$INSTDIR\start_smt.bat" "" "$INSTDIR\SMT_Logo.ico"
SectionEnd

Section "Uninstall"
  SetShellVarContext all
  RMDir /r "$INSTDIR"
  Delete "$DESKTOP\Smart Money Trader.lnk"
  RMDir /r "$SMPROGRAMS\${AppName}"
  DeleteRegKey HKLM "${UninstKey}"
SectionEnd
