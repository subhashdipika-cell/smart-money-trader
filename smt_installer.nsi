; Smart Money Trader - NSIS Installer Script
; Builds a single setup.exe

Unicode true
!include "MUI2.nsh"

!define AppName "Smart Money Trader"
!define AppVersion "1.0"
!define AppPublisher "Subhash Chand Sharma"
!define AppDir "SmartMoneyTrader"
!define UninstKey "Software\Microsoft\Windows\CurrentVersion\Uninstall\SmartMoneyTrader"

Name "${AppName}"
OutFile "installer_output\SmartMoneyTrader_Setup_v${AppVersion}.exe"
InstallDir "$PROGRAMFILES64\${AppDir}"
RequestExecutionLevel admin
SetCompressor /SOLID lzma

!define MUI_ICON "SMT_Logo.ico"
!define MUI_UNICON "SMT_Logo.ico"
!define MUI_ABORTWARNING

; --- Wizard pages ---
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_COMPONENTS
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_RUN
!define MUI_FINISHPAGE_RUN_TEXT "Run first-time setup (installs packages)"
!define MUI_FINISHPAGE_RUN_FUNCTION RunFirstTimeSetup
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "English"

Function RunFirstTimeSetup
  ExecShell "open" "$INSTDIR\setup.bat"
FunctionEnd

; --- Main install (required) ---
Section "Smart Money Trader (required)" SecMain
  SectionIn RO
  SetShellVarContext all

  ; Backend
  SetOutPath "$INSTDIR\backend\app"
  File /r /x "__pycache__" /x "*.pyc" "backend\app\*"
  SetOutPath "$INSTDIR\backend"
  File "backend\trading_executor.py"
  File "backend\requirements.txt"

  ; Frontend
  SetOutPath "$INSTDIR\frontend\src"
  File /r "frontend\src\*"
  SetOutPath "$INSTDIR\frontend\public"
  File /r "frontend\public\*"
  SetOutPath "$INSTDIR\frontend"
  File "frontend\package.json"
  File "frontend\index.html"
  File "frontend\vite.config.js"

  ; Scripts and icon
  SetOutPath "$INSTDIR"
  File "setup.bat"
  File "start_smt.bat"
  File "stop_smt.bat"
  File "SMT_Logo.ico"

  ; Start Menu shortcuts
  CreateDirectory "$SMPROGRAMS\${AppName}"
  CreateShortCut "$SMPROGRAMS\${AppName}\Start Smart Money Trader.lnk" "$INSTDIR\start_smt.bat" "" "$INSTDIR\SMT_Logo.ico"
  CreateShortCut "$SMPROGRAMS\${AppName}\Stop Smart Money Trader.lnk" "$INSTDIR\stop_smt.bat" "" "$INSTDIR\SMT_Logo.ico"
  CreateShortCut "$SMPROGRAMS\${AppName}\First Time Setup.lnk" "$INSTDIR\setup.bat" "" "$INSTDIR\SMT_Logo.ico"
  CreateShortCut "$SMPROGRAMS\${AppName}\Uninstall ${AppName}.lnk" "$INSTDIR\uninstall.exe"

  ; Uninstaller + Add/Remove Programs entry
  WriteUninstaller "$INSTDIR\uninstall.exe"
  WriteRegStr HKLM "${UninstKey}" "DisplayName" "${AppName}"
  WriteRegStr HKLM "${UninstKey}" "DisplayVersion" "${AppVersion}"
  WriteRegStr HKLM "${UninstKey}" "Publisher" "${AppPublisher}"
  WriteRegStr HKLM "${UninstKey}" "DisplayIcon" "$INSTDIR\SMT_Logo.ico"
  WriteRegStr HKLM "${UninstKey}" "UninstallString" "$\"$INSTDIR\uninstall.exe$\""
  WriteRegDWORD HKLM "${UninstKey}" "NoModify" 1
  WriteRegDWORD HKLM "${UninstKey}" "NoRepair" 1
SectionEnd

; --- Optional desktop shortcut ---
Section "Desktop shortcut" SecDesktop
  SetShellVarContext all
  CreateShortCut "$DESKTOP\Smart Money Trader.lnk" "$INSTDIR\start_smt.bat" "" "$INSTDIR\SMT_Logo.ico"
SectionEnd

; --- Uninstall ---
Section "Uninstall"
  SetShellVarContext all
  RMDir /r "$INSTDIR"
  Delete "$DESKTOP\Smart Money Trader.lnk"
  RMDir /r "$SMPROGRAMS\${AppName}"
  DeleteRegKey HKLM "${UninstKey}"
SectionEnd
