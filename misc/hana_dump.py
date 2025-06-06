from machine import Pin,SoftI2C

i2c = SoftI2C(sda=Pin(8),scl=Pin(9),freq=100000)

def dump_regs():
    for i in range(0,0x100):
        print(f"{i:02x} -> {i2c.readfrom_mem(0x70, i, 5)}")
