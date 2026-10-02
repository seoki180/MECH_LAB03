"""밖에서 밀려 들어오는(push) 측정값을 받는 소스.

파일 재생 소스(``nmea.py``)는 세션이 ``read(elapsed)``로 필요한 시점의 값을 꺼내
가는 pull 방식이다. 실장비와 WebSocket은 반대로 장비가 보내는 대로 도착하므로
받아 두는 곳이 필요하다. 이 모듈이 그 경계다.

세션 루프는 소스를 짧은 주기로 ``read`` 하므로, 수신 스레드가 큐에 넣고 ``read``가
그때까지 쌓인 것을 한 번에 꺼내 간다. 도착한 것이 없으면 빈 tuple을 돌려주며, 세션은
그것을 정상으로 보고 다음 주기를 기다린다. **값을 지어내거나 마지막 값을 반복해서
채우지 않는다.** 값이 없는 것과 0인 것은 다르다.

큐는 크기가 정해져 있다. 소비가 밀리면 가장 오래된 표본을 버리고 ``dropped``를 센다.
무한히 늘려 메모리를 삼키는 대신 버린 사실을 남기는 쪽을 택한다. 버린 수는 세션이
보고할 수 있도록 노출한다.

WebSocket 구현체는 아직 없다. 규격(주소, 인증, 메시지 형식, 채널 식별자)이 확정되지
않았으므로 지어내지 않는다. 확정되면 ``StreamingSensorSource``를 상속해 수신 스레드가
``submit()``을 호출하게만 만들면 세션·기록·화면은 그대로 동작한다.
"""

from collections import deque
from threading import Lock


class StreamingSensorSource:
    """push 방식 측정 소스의 공통 부분.

    하위 구현이 할 일은 두 가지뿐이다.
    - ``connect()``에서 수신을 시작하고, 표본이 도착하면 ``submit(sample)``을 호출한다.
    - ``disconnect()``에서 수신을 멈춘다.

    세션은 ``prepare() → read() 반복 → close()`` 순으로 호출한다.
    """

    # 사람이 중지할 때까지 계속 받는다. 끝이 없다.
    continuous = True

    def __init__(self, capacity=10000):
        self._queue = deque(maxlen=capacity)
        self._lock = Lock()
        self.dropped = 0
        self.connected = False

    # --- 하위 구현이 채우는 부분 ---

    def connect(self):
        """수신 시작. 하위 구현이 소켓을 열고 수신 스레드를 띄운다."""

    def disconnect(self):
        """수신 중지. 하위 구현이 소켓을 닫고 스레드를 정리한다."""

    # --- 수신 스레드가 호출하는 부분 ---

    def submit(self, sample):
        """도착한 표본을 넣는다. 수신 스레드에서 호출해도 안전하다."""
        with self._lock:
            if len(self._queue) == self._queue.maxlen:
                # deque(maxlen)이 조용히 버리므로 버린 사실을 직접 센다.
                self.dropped += 1
            self._queue.append(sample)

    # --- 세션 작업 스레드가 호출하는 부분 ---

    def prepare(self):
        self.connect()
        self.connected = True

    def _drain(self):
        """큐에 쌓인 것을 모두 꺼낸다. 하위 구현이 변환을 끼울 때 쓴다."""
        with self._lock:
            if not self._queue:
                return ()
            batch = tuple(self._queue)
            self._queue.clear()
            return batch

    def read(self, session_id, elapsed, sequence, scenario):
        """지금까지 도착한 표본을 모두 꺼낸다. 없으면 빈 tuple."""
        # 세션이 시작되기 전에 남아 있던 표본이 섞이지 않게 현재 세션 것만 남긴다.
        return tuple(s for s in self._drain() if s.session_id in (session_id, None))

    def close(self):
        try:
            self.disconnect()
        finally:
            self.connected = False
