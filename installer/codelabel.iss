; ============================================================
; CodeLabelStudio Windows 安装脚本（Inno Setup 6）
; 编译：ISCC.exe installer\codelabel.iss
; 前置：先运行 scripts\build_client.bat 生成 dist\CodeLabelStudio.exe
;
; 行为约定：
; - 数据目录（%LOCALAPPDATA%\CodeLabelStudio\data）升级与卸载均不覆盖/不删除
; - 卸载时询问是否删除本地数据
; ============================================================

#define MyAppName "CodeLabelStudio"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "CodeLabel Studio"
#define MyAppExeName "CodeLabelStudio.exe"

[Setup]
AppId={{7E5A3C21-9B4D-4F8A-B6E2-C0DE1ABE5710}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=..\dist\installer
OutputBaseFilename=CodeLabelStudio-Setup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Default.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务:"

[Files]
; 单文件可执行（PyInstaller onefile 产物）
Source: "..\dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
; 便携版压缩包一并放入安装目录，供用户选择免安装使用
Source: "..\dist\CodeLabelStudio-Portable.zip"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "启动 CodeLabelStudio"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 卸载时清理日志与缓存，但保留用户数据（见下方 Code 段）
Type: filesandordirs; Name: "{app}\logs"
Type: filesandordirs; Name: "{app}\__pycache__"

[Code]
var
  DataDirPage: TInputDirWizardPage;
  KeepData: Boolean;

function InitializeSetup(): Boolean;
begin
  Result := True;
end;

procedure InitializeWizard();
begin
  { 数据目录页：默认 %LOCALAPPDATA%\CodeLabelStudio\data }
  DataDirPage := CreateInputDirPage(
    wpSelectDir,
    '选择数据目录',
    '本地数据库、会话与授权缓存将保存在以下目录',
    '数据目录在升级与卸载时默认保留，请谨慎选择。' + #13#10 + #13#10 +
      '点击"下一步"继续，或点击"浏览"选择其他位置。',
    False, '');
  DataDirPage.Add('数据目录');
  DataDirPage.Values[0] := ExpandConstant('{localappdata}\{#MyAppName}\data');
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if CurPageID = DataDirPage.ID then
  begin
    if DataDirPage.Values[0] = '' then
    begin
      MsgBox('数据目录不能为空。', mbError, MB_OK);
      Result := False;
    end;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  DataDir: String;
begin
  if CurStep = ssPostInstall then
  begin
    DataDir := DataDirPage.Values[0];
    ForceDirectories(DataDir);
    { 客户端启动时读取 exe 同级 data_dir.txt 定位数据目录 }
    SaveStringToFile(ExpandConstant('{app}\data_dir.txt'), DataDir, False);
    { 同时写入注册表，供卸载程序准确定位（用户可能自选任意目录） }
    RegWriteStringValue(HKCU, 'Software\CodeLabelStudio', 'DataDir', DataDir);
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep = usUninstall then
  begin
    KeepData := True;
    if MsgBox('是否同时删除本地数据（数据库、会话、授权缓存）？' + #13#10 +
              '选择"是"将删除数据目录，选择"否"将保留。',
              mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
      KeepData := False;
  end;

  if CurUninstallStep = usPostUninstall then
  begin
    if not KeepData then
    begin
      { 优先用注册表记录的实际数据目录；缺失时回退默认路径 }
      if not RegQueryStringValue(HKCU, 'Software\CodeLabelStudio',
                                 'DataDir', DataDir) then
        DataDir := ExpandConstant('{localappdata}\{#MyAppName}');
      if (DataDir <> '') and DirExists(DataDir) then
        DelTree(DataDir, True, True, True);
    end;
    RegDeleteValue(HKCU, 'Software\CodeLabelStudio', 'DataDir');
    RegDeleteKeyIfEmpty(HKCU, 'Software\CodeLabelStudio');
  end;
end;
