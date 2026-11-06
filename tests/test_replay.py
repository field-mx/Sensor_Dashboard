import importlib.util

def clock():
    assert importlib.util.find_spec('sensor_demo.replay') is not None,'Replay controller not implemented'
    from sensor_demo.replay import ReplayClock
    return ReplayClock()

def test_pause_reset_speed_and_end():
    c=clock();c.load(5);c.play()
    assert c.advance()==0
    c.pause();assert c.advance() is None and c.seq==0
    c.set_speed(5);c.play();assert c.advance()==1
    generation=c.generation;c.reset()
    assert c.generation==generation+1 and c.seq==-1 and not c.running
    c.play();assert [c.advance() for _ in range(6)]==[0,1,2,3,4,None]
    assert not c.running

def test_speeds_keep_identical_source_sequence_and_backpressure():
    for speed in [1,2,5,10]:
        c=clock();c.load(3);c.set_speed(speed);c.play()
        c.blocked=True;assert c.advance() is None and c.seq==-1
        c.blocked=False;assert [c.advance() for _ in range(3)]==[0,1,2]
