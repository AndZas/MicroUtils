import threading
import time
import unittest
import uuid
from local_control import LocalControl


class LocalControlTests(unittest.TestCase):
    def test_request_is_consumed_once_and_does_not_start_sdk_client(self):
        name='SoundControlTest-'+uuid.uuid4().hex
        control=LocalControl(name)
        try:
            self.assertTrue(LocalControl.request(name,'show'))
            self.assertTrue(control.pending('show'))
            self.assertFalse(control.pending('show'))
            self.assertFalse(control.pending('quit'))
        finally:
            control.close()
        self.assertFalse(LocalControl.request(name,'show'))

    def test_diagnostic_request_waits_for_the_existing_worker_response(self):
        name='SoundControlTest-'+uuid.uuid4().hex
        control=LocalControl(name)
        def worker():
            deadline=time.monotonic()+2
            while time.monotonic()<deadline:
                if control.pending('status'):
                    control.ready()
                    return
                time.sleep(.005)
        thread=threading.Thread(target=worker)
        thread.start()
        try:
            self.assertTrue(LocalControl.request(name,'status',2000))
            thread.join(2)
            self.assertFalse(thread.is_alive())
        finally:
            control.close()


if __name__=='__main__':unittest.main()
