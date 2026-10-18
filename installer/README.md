# installer/ — Windows 安装与打包

阶段 9 实现，产出：

- `CodeLabelStudio-Setup.exe`（Inno Setup：安装向导、目录选择、快捷方式、卸载注册、升级不覆盖用户数据、卸载时询问是否保留数据）
- `CodeLabelStudio-Portable.zip`（绿色便携版：解压即用、免管理员权限、数据保存在便携目录内）

注意：本机当前未安装 Inno Setup（见审计报告风险清单 R4），阶段 9 开始前需先安装。
