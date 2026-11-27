# 发票省纸合并

首页使用矢量排版。右上角“返回旧版”进入 `/legacy`，原有 `/v2` 入口也可访问新版。页面直接显示使用说明和单向反馈输入框。

新版可以混选 OFD、PDF、JPG 和 JPEG。每个文件取第一页，按上传列表顺序每三份排在一张 A4 上。PDF 原有的文字和矢量图形可保留。OFD 先在本机用 OFDRW 转换首页，再交给 PyMuPDF 拼版。旧版使用 600 DPI 图片拼版，接受 PDF 和 JPEG。合并结果用于打印，验签请使用原件。

## 本机运行

需要 Python 3.10 或更新版本、Maven、Java 17 或 21。可用 `INVOICES_JAVA` 指定 Java 可执行文件的绝对路径。

```sh
mvn -f ofd/pom.xml package
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python app.py
```

打开 `http://127.0.0.1:1910/`。用 `examples/` 里的合成文件试传 OFD 与 PDF。

## Linux 部署

`deploy/` 中包含 Gunicorn、Nginx 和两个 systemd 服务的配置示例。根据主机调整安装路径、服务账号、监听端口和公开健康检查地址。网页使用 Gunicorn，在 Nginx 后监听回环地址。按服务配置创建两个服务账号和共享状态组。共享数据目录需要组写权限和 setgid，服务目录应由部署账号管理。

如需迁移旧版用量，从历史输出 PDF 的文件名汇总日期，再导入 SQLite。重复导入会更新历史计数，已记录的新版用量保留。

```sh
python deploy/legacy-usage.py /path/to/old/combined > daily-usage.json
flask --app app seed-demo --source daily-usage.json
```

页脚按台北时间显示当前月和前两个月的合并产物数。成功生成一个合并 PDF 计一次。SQLite 默认为 `instance/usage.sqlite3`，可用 `INVOICES_USAGE_DB` 指定。

## Bark 通知

用单独的进程运行 `python monitor.py`。它投递反馈和故障通知，每 20 秒检查一次首页。可用 `INVOICES_HEALTH_URL` 指定检查地址。网页和监测进程需要共享通知数据库，默认位于 `instance/alerts.sqlite3`，可用 `INVOICES_ALERT_DB` 指定。

把 Bark 设备密钥写入 `instance/bark.key`，或用 `INVOICES_BARK_KEY_FILE` 指向仅监测进程可读的文件。`INVOICES_SYSTEMD_UNIT` 指向网页服务时，可发现两次健康检查之间的异常重启。macOS 可用 `INVOICES_WEB_JOB` 指定 launchd 服务。投递失败的通知会重试。短时间内同类处理错误和频繁提交的反馈会限频。

监测进程与被监测网站运行在同一主机时，主机关机或完全断网期间无法即时推送。
