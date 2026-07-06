# udp.py
# AD7616 16通道 UDP 实时波形显示程序
# 直接运行：python udp.py
#
# 默认设置：
#   本机监听端口：9090
#   通道数：16
#   每包长度：1024 Byte
#   数据格式：int16，小端序
#   每包帧数：1024 / (16 * 2) = 32帧

import socket
import threading
import queue
import time
import sys

import numpy as np
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation


# ================= 用户常用参数 =================
UDP_IP = "0.0.0.0"
UDP_PORT = 9090

ADC_CHANNELS = 16
BYTES_PER_SAMPLE = 2
FRAME_BYTES = ADC_CHANNELS * BYTES_PER_SAMPLE
DEFAULT_PACKET_BYTES = 1024

# 如果波形数值明显不对，把这里改成 ">i2" 试试
# "<i2" = little-endian int16
# ">i2" = big-endian int16
ADC_DTYPE = "<i2"

# 屏幕上保留最近多少个采样帧
WINDOW_SAMPLES = 2000

# 图像刷新间隔，单位 ms
REFRESH_MS = 50

# UDP接收缓存，越大越不容易丢包
SOCKET_RCVBUF = 4 * 1024 * 1024

# 队列最大缓存包数。显示来不及时会丢旧包，避免越积越卡
QUEUE_MAX_PACKETS = 2000


# ================= 全局状态 =================
pkt_queue = queue.Queue(maxsize=QUEUE_MAX_PACKETS)
stop_flag = False

packet_count = 0
drop_count = 0
byte_count = 0
last_packet_time = 0.0


def udp_receiver():
    """后台线程：接收UDP数据包，并放入队列。"""
    global packet_count, drop_count, byte_count, last_packet_time, stop_flag

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, SOCKET_RCVBUF)
    sock.bind((UDP_IP, UDP_PORT))
    sock.settimeout(1.0)

    print(f"[UDP] Listening on {UDP_IP}:{UDP_PORT}")
    print("[UDP] Waiting for AD7616 packets...")

    while not stop_flag:
        try:
            data, addr = sock.recvfrom(65535)
        except socket.timeout:
            continue
        except OSError:
            break

        # Dianqi程序启动时可能会发一个字符串唤醒包，不是ADC数据，直接忽略
        if len(data) < FRAME_BYTES or (len(data) % FRAME_BYTES) != 0:
            continue

        try:
            arr = np.frombuffer(data, dtype=np.dtype(ADC_DTYPE))
        except ValueError:
            continue

        if arr.size % ADC_CHANNELS != 0:
            continue

        frames = arr.reshape((-1, ADC_CHANNELS)).copy()

        # 队列满时丢掉最旧的包，保证显示尽量跟得上实时数据
        if pkt_queue.full():
            try:
                pkt_queue.get_nowait()
                drop_count += 1
            except queue.Empty:
                pass

        try:
            pkt_queue.put_nowait(frames)
            packet_count += 1
            byte_count += len(data)
            last_packet_time = time.time()
        except queue.Full:
            drop_count += 1

    sock.close()


def ordered_buffer(buf, write_pos, filled):
    """把环形缓冲区整理成时间顺序。"""
    if filled < WINDOW_SAMPLES:
        return buf[:filled].copy()
    return np.vstack((buf[write_pos:], buf[:write_pos]))


def main():
    global stop_flag

    # 环形缓冲区：行=采样帧，列=16通道
    buf = np.zeros((WINDOW_SAMPLES, ADC_CHANNELS), dtype=np.int16)
    write_pos = 0
    filled = 0

    rx_thread = threading.Thread(target=udp_receiver, daemon=True)
    rx_thread.start()

    fig, axes = plt.subplots(4, 4, figsize=(14, 8), sharex=True)
    axes = axes.ravel()

    x = np.arange(WINDOW_SAMPLES)
    lines = []

    for ch in range(ADC_CHANNELS):
        ax = axes[ch]
        line, = ax.plot(x, np.zeros(WINDOW_SAMPLES, dtype=np.int16), linewidth=0.8)
        lines.append(line)

        ax.set_title(f"CH{ch}", fontsize=10)
        ax.grid(True, linewidth=0.4)
        ax.set_xlim(0, WINDOW_SAMPLES - 1)
        ax.set_ylim(-32768, 32767)

        if ch % 4 == 0:
            ax.set_ylabel("Count")
        if ch >= 12:
            ax.set_xlabel("Sample")

    fig.suptitle("AD7616 16-Channel UDP Viewer | Port 9090", fontsize=12)
    plt.tight_layout(rect=[0, 0.03, 1, 0.95])

    last_stat_time = time.time()
    last_packet_count = 0

    def update(_frame):
        nonlocal write_pos, filled, last_stat_time, last_packet_count

        updated = False

        # 一次刷新尽量把队列里积压的数据都取出来
        while True:
            try:
                frames = pkt_queue.get_nowait()
            except queue.Empty:
                break

            n = frames.shape[0]
            if n >= WINDOW_SAMPLES:
                buf[:, :] = frames[-WINDOW_SAMPLES:, :]
                write_pos = 0
                filled = WINDOW_SAMPLES
            else:
                end_pos = write_pos + n
                if end_pos <= WINDOW_SAMPLES:
                    buf[write_pos:end_pos, :] = frames
                else:
                    first = WINDOW_SAMPLES - write_pos
                    buf[write_pos:, :] = frames[:first, :]
                    buf[:end_pos % WINDOW_SAMPLES, :] = frames[first:, :]
                write_pos = end_pos % WINDOW_SAMPLES
                filled = min(WINDOW_SAMPLES, filled + n)

            updated = True

        if not updated and filled == 0:
            return lines

        y_all = ordered_buffer(buf, write_pos, filled)
        show_len = y_all.shape[0]

        if show_len == 0:
            return lines

        # 不足窗口长度时，左侧补0，右侧显示最新数据
        plot_data = np.zeros((WINDOW_SAMPLES, ADC_CHANNELS), dtype=np.int16)
        plot_data[-show_len:, :] = y_all

        for ch in range(ADC_CHANNELS):
            y = plot_data[:, ch]
            lines[ch].set_ydata(y)

            # 每个小框独立缩放Y轴，避免某一路幅值过大影响其他路观察
            recent = y[-min(show_len, 500):]
            ymin = int(np.min(recent))
            ymax = int(np.max(recent))
            if ymin == ymax:
                margin = 100
            else:
                margin = max(100, int((ymax - ymin) * 0.15))

            axes[ch].set_ylim(ymin - margin, ymax + margin)

        now = time.time()
        if now - last_stat_time >= 1.0:
            pps = packet_count - last_packet_count
            last_packet_count = packet_count
            last_stat_time = now

            age = now - last_packet_time if last_packet_time > 0 else -1
            if age >= 0:
                title = (f"AD7616 16-Channel UDP Viewer | Port {UDP_PORT} | "
                         f"packets={packet_count} | pps={pps} | drops={drop_count} | last={age:.1f}s ago")
            else:
                title = f"AD7616 16-Channel UDP Viewer | Port {UDP_PORT} | waiting for data..."
            fig.suptitle(title, fontsize=12)

        return lines

    ani = FuncAnimation(fig, update, interval=REFRESH_MS, blit=False)

    try:
        plt.show()
    except KeyboardInterrupt:
        pass
    finally:
        stop_flag = True
        print("\n[EXIT] viewer stopped")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        stop_flag = True
        print(f"[ERROR] {e}")
        sys.exit(1)
