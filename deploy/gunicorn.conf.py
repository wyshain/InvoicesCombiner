from alerts import notify

bind = "127.0.0.1:1911"
workers = 2
timeout = 100
control_socket_disable = True


def worker_abort(worker):
    try:
        notify("发票工具 · 处理超时", "合并请求超过处理时限，网页服务已重新启动处理进程。", window=60)
    except Exception:
        worker.log.exception("Could not queue timeout alert")
