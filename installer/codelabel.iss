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
; 可选：勾选“完成后打开打印机设置向导”时，以 --printer-setup 参数启动应用（不安装任何驱动）
Filename: "{app}\{#MyAppExeName}"; Parameters: "--printer-setup"; Description: "完成后打开打印机设置向导"; Flags: nowait postinstall skipifsilent; Check: DriverSetupChecked

[UninstallDelete]
; 卸载时清理日志与缓存，但保留用户数据（见下方 Code 段）
Type: filesandordirs; Name: "{app}\logs"
Type: filesandordirs; Name: "{app}\__pycache__"

[Code]
var
  DataDirPage: TInputDirWizardPage;
  KeepData: Boolean;
  { 打印环境检测页（只读检测，不安装任何驱动） }
  EnvCheckPage: TWizardPage;
  EnvMemo: TNewMemo;
  { 打印机驱动（可选）说明页 }
  DriverPage: TWizardPage;
  DriverLabel: TNewStaticText;
  DriverCheck: TNewCheckBox;

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

  { 打印环境检测页（位于数据目录页之后）：只读枚举本机队列，不安装驱动 }
  EnvCheckPage := CreateMemoPage(
    DataDirPage.ID,
    '打印环境检测',
    '仅检测本机已安装的打印队列，本步骤不安装任何驱动',
    '下列信息来自系统 Get-Printer（队列名 / 驱动名 / 端口 / 状态），供核对使用。' +
      #13#10 + '若列表为空，表示当前 Windows 中未检测到打印机队列。',
    EnvMemo);
  EnvMemo.Lines.Text := '正在检测本机打印机队列…';
  EnvMemo.ReadOnly := True;
  EnvMemo.ScrollBars := ssVertical;

  { 打印机驱动（可选）说明页（位于打印环境检测页之后）：只给指引，不装驱动 }
  DriverPage := CreateCustomPage(
    EnvCheckPage.ID,
    '打印机驱动（可选）',
    '驱动不进入主安装流程，需从厂商官方渠道单独获取并安装');
  DriverLabel := TNewStaticText.Create(WizardForm);
  DriverLabel.Parent := DriverPage.Surface;
  DriverLabel.Left := 0;
  DriverLabel.Top := 0;
  DriverLabel.Width := ScaleX(430);
  DriverLabel.AutoSize := False;
  DriverLabel.WordWrap := True;
  DriverLabel.Height := ScaleY(230);
  DriverLabel.Caption :=
    '说明：' + #13#10 +
    '1. 本安装包不携带、不安装任何打印机驱动；驱动安装不进入主安装流程。' + #13#10 +
    '2. 请从打印机厂商官方支持/下载页按完整型号单独下载官方 Windows 驱动安装程序，' +
    '不要使用第三方驱动站或来路不明的安装包。' + #13#10 +
    '3. 安装前请核对厂商、型号、官方来源页面与兼容性说明（适用的 Windows 版本）。' + #13#10 +
    '4. 驱动安装需要管理员权限：右键官方安装程序选择"以管理员身份运行"，' +
    '在 UAC 弹窗中点"是"确认；本程序不做静默安装，不关闭驱动签名检查，' +
    '不修改组策略或任何安全设置。' + #13#10 +
    '5. 若驱动安装失败：先在 Windows 中卸载残留驱动 → 重启电脑 → 从官方源' +
    '重新下载并重装 → 检查打印队列是否恢复。' + #13#10;
  DriverCheck := TNewCheckBox.Create(WizardForm);
  DriverCheck.Parent := DriverPage.Surface;
  DriverCheck.Left := 0;
  DriverCheck.Top := ScaleY(240);
  DriverCheck.Width := ScaleX(430);
  DriverCheck.Caption := '完成后打开打印机设置向导';
  DriverCheck.Checked := False;
end;

{ 供 [Run] 段 Check 使用：仅当用户勾选时才以 --printer-setup 启动应用 }
function DriverSetupChecked: Boolean;
begin
  Result := DriverCheck.Checked;
end;

{ 进入打印环境检测页时，用 PowerShell 只读枚举队列并写入 memo；失败不阻断安装 }
procedure CurPageChanged(CurPageID: Integer);
var
  PSPath, Cmd, OutFile: String;
  Ok: Boolean;
  ResultCode: Integer;
begin
  if CurPageID <> EnvCheckPage.ID then
    Exit;
  OutFile := ExpandConstant('{tmp}\printers.txt');
  PSPath := ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe');
  Cmd := '-NoProfile -Command "Get-Printer | ' +
    'Format-Table -AutoSize Name,DriverName,PortName,PrinterStatus | ' +
    'Out-File -FilePath ''' + OutFile + ''' -Encoding utf8"';
  Ok := Exec(PSPath, Cmd, '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  EnvMemo.Lines.Clear;
  if Ok and FileExists(OutFile) then
  begin
    EnvMemo.Lines.LoadFromFile(OutFile);
    if EnvMemo.Lines.Count = 0 then
      EnvMemo.Lines.Text := '未检测到打印机（本步骤仅检测环境，不安装任何驱动）。';
  end
  else
    EnvMemo.Lines.Text :=
      '未能通过 Get-Printer 读取队列（本步骤仅检测环境，不安装任何驱动；' +
      '可在 Windows"设置 → 蓝牙和设备 → 打印机"中手动查看）。';
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
