; Smart Money Trader - Self-contained installer (no Python/Node preinstall needed)
Unicode true
!include "MUI2.nsh"

!define AppName "Smart Money Trader"
!define AppVersion "2.0"
!define UninstKey "Software\Microsoft\Windows\CurrentVersion\Uninstall\SmartMoneyTrader"

Name "${AppName}"
OutFile "SmartMoneyTrader_Setup_v2.0_SelfContained.exe"
InstallDir "$PROGRAMFILES64\SmartMoneyTrader"
RequestExecutionLevel admin
SetCompressor /SOLID lzma

!define MUI_ICON "SMT_Logo.ico"
!define MUI_UNICON "SMT_Logo.ico"
!define MUI_ABORTWARNING

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_COMPONENTS
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_RUN
!define MUI_FINISHPAGE_RUN_TEXT "Run first-time setup now (downloads 11 MB runtime, installs bundled packages)"
!define MUI_FINISHPAGE_RUN_FUNCTION RunFirstTimeSetup
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"

Function RunFirstTimeSetup
  ExecShell "open" "$INSTDIR\setup_runtime.bat"
FunctionEnd

Section "Smart Money Trader (required)" SecMain
  SectionIn RO
  SetShellVarContext all

  SetOutPath "$INSTDIR\backend"
  File /r /x "__pycache__" /x "*.pyc" "backend\*"

  SetOutPath "$INSTDIR\frontend\dist"
  File /r "frontend\dist\*"

  SetOutPath "$INSTDIR\wheels"
  File /r "wheels\*"

  SetOutPath "$INSTDIR\docs"
  File "docs\SMT_User_Manual.pdf"

  SetOutPath "$INSTDIR"
  File "start_smt.bat"
  File "stop_smt.bat"
  File "setup_runtime.bat"
  File "SMT_Logo.ico"

  CreateDirectory "$SMPROGRAMS\${AppName}"
  CreateShortCut "$SMPROGRAMS\${AppName}\Start Smart Money Trader.lnk" "$INSTDIR\start_smt.bat" "" "$INSTDIR\SMT_Logo.ico"
  CreateShortCut "$SMPROGRAMS\${AppName}\Stop Smart Money Trader.lnk" "$INSTDIR\stop_smt.bat" "" "$INSTDIR\SMT_Logo.ico"
  CreateShortCut "$SMPROGRAMS\${AppName}\First Time Setup.lnk" "$INSTDIR\setup_runtime.bat" "" "$INSTDIR\SMT_Logo.ico"
  CreateShortCut "$SMPROGRAMS\${AppName}\User Manual.lnk" "$INSTDIR\docs\SMT_User_Manual.pdf"
  CreateShortCut "$SMPROGRAMS\${AppName}\Uninstall ${AppName}.lnk" "$INSTDIR\uninstall.exe"

  WriteUninstaller "$INSTDIR\uninstall.exe"
  WriteRegStr HKLM "${UninstKey}" "DisplayName" "${AppName}"
  WriteRegStr HKLM "${UninstKey}" "DisplayVersion" "${AppVersion}"
  WriteRegStr HKLM "${UninstKey}" "Publisher" "Subhash Chand Sharma"
  WriteRegStr HKLM "${UninstKey}" "DisplayIcon" "$INSTDIR\SMT_Logo.ico"
  WriteRegStr HKLM "${UninstKey}" "UninstallString" "$\"$INSTDIR\uninstall.exe$\""
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
