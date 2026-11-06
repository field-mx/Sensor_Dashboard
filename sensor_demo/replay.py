from uuid import uuid4

class ReplayClock:
    def __init__(self):
        self.count=0;self.seq=-1;self.speed=1;self.generation=0
        self.running=False;self.blocked=False;self.run_id=str(uuid4())
    def load(self,count):
        self.count=count;self.run_id=str(uuid4());self.reset()
    def play(self):
        self.running=self.count>0 and self.seq<self.count-1
    def pause(self):self.running=False
    def reset(self):
        self.seq=-1;self.generation+=1;self.running=False;self.blocked=False
    def set_speed(self,speed):
        if speed not in (1,2,5,10):raise ValueError('Unsupported playback speed')
        self.speed=speed
    def advance(self):
        if not self.running or self.blocked:return None
        if self.seq+1>=self.count:self.running=False;return None
        self.seq+=1
        if self.seq==self.count-1:self.running=False
        return self.seq
