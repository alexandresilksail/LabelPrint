"""打印机适配器架构。

- :mod:`client.printing.adapters.base`        适配器统一接口（契约）
- :mod:`client.printing.adapters.universal`   通用 Windows 打印适配器（队列路径）
- :mod:`client.printing.adapters.zpl`         ZPL 专用协议适配器
- :mod:`client.printing.adapters.tspl`         TSPL 专用协议适配器
- :mod:`client.printing.adapters.cpcl`         CPCL 专用协议适配器
- :mod:`client.printing.adapters.transports`    USB/网络/蓝牙传输层抽象

设计约束（全项目强制）：
1. 通用打印与专用协议严格分离；专用适配器**不得**绕过统一的
   尺寸/静区/任务/错误处理机制（必须经 :meth:`BasePrinterAdapter.validate_job`）。
2. 只有源码、设备文档与测试证据支持时，才可声明某协议/型号"已适配"；
   未经真实设备测试的适配器 ``capabilities.verified_with_hardware`` 必须为 False。
"""
