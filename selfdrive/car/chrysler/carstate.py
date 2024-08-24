from cereal import car
from openpilot.common.conversions import Conversions as CV
from opendbc.can.parser import CANParser
from opendbc.can.can_define import CANDefine
from openpilot.selfdrive.car.chrysler.chryslerlonghelper import SET_SPEED_MIN
from openpilot.selfdrive.car.interfaces import CarStateBase
from openpilot.selfdrive.car.chrysler.values import DBC, STEER_THRESHOLD, HYBRID_CARS, RAM_CARS
from openpilot.common.params import Params
from common.cached_params import CachedParams


class CarState(CarStateBase):
  def __init__(self, CP):
    super().__init__(CP)
    self.CP = CP
    can_define = CANDefine(DBC[CP.carFingerprint]["pt"])
    if CP.carFingerprint in RAM_CARS:
      self.shifter_values = can_define.dv["Transmission_Status"]["Gear_State"]
    else:
      self.shifter_values = can_define.dv["GEAR"]["PRNDL"]
    self.acc_on_button = False
    self.veh_on_timer = 0
    self.axle_torq = 0

    # long control
    self.longControl = False
    self.cachedParams = CachedParams()
    self.das_3 = None
    self.das_5 = None
    self.longEnabled = False
    self.longControl = False
    self.gasRpm = None
    self.allowLong = True # CP.carFingerprint in (CAR.JEEP_CHEROKEE, CAR.JEEP_CHEROKEE_2019)
    self.torqMin = None
    self.torqMax = None
    self.wheelTorqMin = None
    self.wheelTorqMax = None
    self.transmission_gear = None
    self.engine_torque = None  

  def update(self, cp, cp_cam):

    ret = car.CarState.new_message()

    # lock info
    ret.doorOpen = any([cp.vl["BCM_1"]["DOOR_OPEN_FL"],
                        cp.vl["BCM_1"]["DOOR_OPEN_FR"],
                        cp.vl["BCM_1"]["DOOR_OPEN_RL"],
                        cp.vl["BCM_1"]["DOOR_OPEN_RR"]])
    ret.seatbeltUnlatched = cp.vl["ORC_1"]["SEATBELT_DRIVER_UNLATCHED"] == 1

    ret.brakePressed = cp.vl["ESP_1"]["BRAKE_PEDAL"] == 1  # driver-only
    ret.brake = cp.vl["ESP_8"]["BRAKE_VAL_TOTAL"]
    # ret.brakeLights = bool(cp.vl["ESP_1"]["BRAKE_LIGHT"])
    ret.gas = cp.vl["ACCEL_GAS_22F"]["GAS_PEDAL_POS"]
    ret.gasPressed = ret.gas > 1e-5

    ret.espDisabled = (cp.vl["TRACTION_BUTTON"]["TRACTION_OFF"] == 1)

    ret.wheelSpeeds.fl = cp.vl["ESP_6"]["WHEEL_SPEED_FL"]
    ret.wheelSpeeds.rr = cp.vl["ESP_6"]["WHEEL_SPEED_RR"]
    ret.wheelSpeeds.rl = cp.vl["ESP_6"]["WHEEL_SPEED_RL"]
    ret.wheelSpeeds.fr = cp.vl["ESP_6"]["WHEEL_SPEED_FR"]
    ret.vEgoRaw = cp.vl["ESP_8"]["VEHICLE_SPEED_KPH"] * CV.KPH_TO_MS
    ret.vEgo, ret.aEgo = self.update_speed_kf(ret.vEgoRaw)
    ret.standstill = bool(cp.vl["ESP_8"]["STANDSTILL"])
    #self.long_accel = cp.vl["INERTIAL_SENSOR"]["LONG_ACCEL"]
    ret.gearShifter = self.parse_gear_shifter(self.shifter_values.get(cp.vl["GEAR"]["PRNDL"], None))

    # button presses
    ret.leftBlinker, ret.rightBlinker = self.update_blinker_from_stalk(200, cp.vl["STEERING_LEVERS"]["TURN_SIGNALS"] == 1,
                                                                       cp.vl["STEERING_LEVERS"]["TURN_SIGNALS"] == 2)

    # steering wheel
    ret.steeringAngleDeg = cp.vl["STEERING"]["STEERING_ANGLE"] + cp.vl["STEERING"]["STEERING_ANGLE_HP"]
    ret.steeringRateDeg = cp.vl["STEERING"]["STEERING_RATE"]

    self.acc_on_button_prev = self.acc_on_button
    self.acc_on_button = bool(cp.vl["WHEEL_BUTTONS"]["ACC_BUTTON_ON"])
    self.reg_cc_on_button = bool(cp.vl["WHEEL_BUTTONS"]["REG_CC_BUTTON_ON"])

    # ret.cruiseState.enabled = bool(cp.vl["ACC_2"]["ACC_ENABLED"])  # ACC is green.
    # ret.cruiseState.available = bool(cp.vl["ACC_2"]["ACC_AVAILABLE"])
    # ret.cruiseState.speed = max(cp.vl["DASHBOARD"]["ACC_SET_SPEED_MPH"] * CV.MPH_TO_MS, SET_SPEED_MIN)
    # CRUISE_STATE is a three bit msg, 0 is off, 1 and 2 are Non-ACC mode, 3 and 4 are ACC mode, find if there are other states too
    # ret.cruiseState.nonAdaptive = cp.vl["DASHBOARD"]["CRUISE_STATE"] in (1, 2)



    self.longControl = True #(self.CP.experimentalLongitudinalAvailable and cp.vl["DAS_4"]["ACC_STATE"] == 0
                        #and self.cachedParams.get_bool('ExperimentalLongitudinalEnabled', 1000))
    if self.longControl:
      ret.jvePilotCarState.longControl = True
      ret.cruiseState.enabled = self.longEnabled
      ret.cruiseState.available = True
      ret.cruiseState.nonAdaptive = False
      ret.cruiseState.standstill = False
      ret.accFaulted = False
      self.torqMin = cp.vl["DAS_3"]["ENGINE_TORQUE_REQUEST"]
      self.torqMax = cp.vl["ECM_TRQ"]["ENGINE_TORQ_MAX"]
      self.transmission_gear = int(cp.vl['TCM_A7']["CurrentGear"])
      self.gasRpm = cp.vl["ENGINE_RPM_HEV"]["ENGINE_RPM"]
      self.engine_torque = cp.vl["ENGINE_RPM_HEV"]["ENGINE_TORQUE"]
      if self.CP.carFingerprint in HYBRID_CARS:
        self.wheelTorqMin = cp.vl["AXLE_TORQ"]["AXLE_TORQ_MIN"]
        self.wheelTorqMax = cp.vl["AXLE_TORQ"]["AXLE_TORQ_MAX"]
    else:
      self.longEnabled = False
      ret.jvePilotCarState.longControl = False
      ret.cruiseState.available = cp_cruise.vl["DAS_3"]["ACC_AVAILABLE"] == 1
      ret.cruiseState.enabled = cp_cruise.vl["DAS_3"]["ACC_ACTIVE"] == 1
      ret.cruiseState.speed = cp_cruise.vl["DAS_4"]["ACC_SET_SPEED_KPH"] * CV.KPH_TO_MS
      ret.cruiseState.nonAdaptive = cp_cruise.vl["DAS_4"]["ACC_STATE"] in (1, 2)  # 1 NormalCCOn and 2 NormalCCSet
      ret.cruiseState.standstill = cp_cruise.vl["DAS_3"]["ACC_STANDSTILL"] == 1
      ret.accFaulted = cp_cruise.vl["DAS_3"]["ACC_FAULTED"] != 0

    self.das_3 = cp.vl['DAS_3']
    self.das_5 = cp.vl['ACC_1']


    
    self.desiredExperimentalToggleStatus = False
    if cp.vl["DAS_4"]["CRUISE_ICON"] in (2, 8, 12):
      ret.cruiseState.followSettings = 1
    elif cp.vl["DAS_4"]["CRUISE_ICON"] in (3, 9, 13):
      ret.cruiseState.followSettings = 2
    elif cp.vl["DAS_4"]["CRUISE_ICON"] in (4, 10, 14):
      ret.cruiseState.followSettings = 3
    else:
      ret.cruiseState.followSettings = 4
      self.desiredExperimentalToggleStatus = True

    if self.desiredExperimentalToggleStatus != Params().get_bool('ExperimentalMode'):
      Params().put_bool("ExperimentalMode", self.desiredExperimentalToggleStatus)
    if self.desiredExperimentalToggleStatus != Params().get_bool('ExperimentalLongitudinalEnabled'):
      Params().put_bool("ExperimentalLongitudinalEnabled", self.desiredExperimentalToggleStatus)

    ret.steeringTorque = cp.vl["EPS_2"]["TORQUE_DRIVER"]/4
    ret.steeringTorqueEps = cp.vl["EPS_2"]["TORQUE_MOTOR"]/4 #if Params().get_bool("ChryslerMangoLat") else cp.vl["EPS_2"]["TORQUE_MOTOR"]
    ret.steeringPressed = abs(ret.steeringTorque) > STEER_THRESHOLD/4
    # cruise state
    #cp_cruise = cp_cam if self.CP.carFingerprint in RAM_CARS else cp

    self.steerFaultPermanent = cp.vl["EPS_2"]["LKAS_STEER_FAULT"] == 4
    self.apaFault = cp.vl["EPS_2"]["APA_STEER_FAULT"] == 1
    self.apasteerOn = cp.vl["EPS_2"]["APA_ACTIVE"] == 1

    ret.genericToggle = bool(cp.vl["STEERING_LEVERS"]["HIGH_BEAM_FLASH"])

    if self.CP.enableBsm:
      ret.leftBlindspot = cp.vl["BLIND_SPOT_WARNINGS"]["BLIND_SPOT_LEFT"] == 1
      ret.rightBlindspot = cp.vl["BLIND_SPOT_WARNINGS"]["BLIND_SPOT_RIGHT"] == 1

    self.lkas_counter = cp_cam.vl["LKAS_COMMAND"]["COUNTER"]
    self.lkas_status_ok = cp_cam.vl["LKAS_HEARTBIT"]["LKAS_BUTTON_LED"]
    self.apa_steer_status = cp.vl["AUTO_PARK_REQUEST"]["APA_STEER_ACT"] == 1
    if self.CP.enablehybridEcu:
       if cp.vl["HYBRID_ECU"]["VEH_ON"] == 1:
         self.veh_on_timer += 1
       else:
         self.veh_on_timer = 0
       self.veh_on = self.veh_on_timer >= 50
       self.axle_torq = cp.vl["AXLE_TORQ"]["AXLE_TORQ"]
       self.axle_torq_max = cp.vl["AXLE_TORQ"]["AXLE_TORQ_MAX"]
       self.axle_torq_min = cp.vl["AXLE_TORQ"]["AXLE_TORQ_MIN"]
       self.hybrid_power_meter = cp.vl["HEV_HMI"]["ELEC_MODE_PERCENT"]
    else:
      self.veh_on_timer += 1
      self.veh_on = self.veh_on_timer >= 200
      self.axle_torq_min = 20.
      self.axle_torq_max = 300.
      self.hybrid_power_meter = 1

    self.acc_hold = bool(cp.vl["DAS_3"]["ACC_STANDSTILL"])
    self.lead_dist = cp.vl["DAS_4"]["SPEED_DIGITAL"]
    self.wheel_button_counter = cp.vl["WHEEL_BUTTONS"]["COUNTER"]

    self.tcs_active = bool(cp.vl["ESC_ACC_COPY"]["TCS_ACTIVE"])

    self.acc_cancel_button = bool(cp.vl["WHEEL_BUTTONS"]["ACC_CANCEL"]) or self.reg_cc_on_button or self.tcs_active
    self.acc_resume_button = bool(cp.vl["WHEEL_BUTTONS"]["ACC_RESUME"])
    self.acc_setplus_button = bool(cp.vl["WHEEL_BUTTONS"]["ACC_SPEED_INC"])
    self.acc_setminus_button = bool(cp.vl["WHEEL_BUTTONS"]["ACC_SPEED_DEC"])
    self.acc_followdec_button = bool(cp.vl["WHEEL_BUTTONS"]["ACC_FOLLOW_DEC"])
    self.acc_followinc_button = bool(cp.vl["WHEEL_BUTTONS"]["ACC_FOLLOW_INC"])

    self.acc_button_pressed = self.acc_cancel_button or self.acc_resume_button or self.acc_setplus_button or \
                              self.acc_setminus_button or self.acc_followdec_button or self.acc_followinc_button

    #ret.accgasOverride = bool(cp.vl["ACCEL_RELATED_120"]["ACC_OVERRIDE"])
    self.accbrakeFaulted = ((cp.vl["ESP_1"]["ACC_BRAKE_FAIL"]) > 0) or ((cp.vl["ACC_ERROR"]["ACC_ERROR"]) > 0)
    self.accengFaulted = (cp.vl["ACCEL_RELATED_120"]["ACC_ENG_OK"]) == 0

    return ret

  @staticmethod
  def get_cruise_messages():
    messages = [
      ("DAS_3", 50),
      ("DAS_4", 50),
      ("ACC_1", 50),
    ]
    return messages

  @staticmethod
  def get_hybrid_messages():
    messages = [
      ("AXLE_TORQ", 50),
    ]
    return messages

  
  @staticmethod
  def get_can_parser(CP):


    messages = [
      # sig_address, frequency das4 a das3 tu nebylo
      ("ESP_1", 50),
      ("EPS_2", 100),
      # ("SPEED_1", 100),
      ("ESP_6", 50),
      ("STEERING", 100),
      #("ACC_2", 50),
      #("GEAR", 50),
      ("ACCEL_GAS_134", 50),
      #("DASHBOARD", 15),
      ("STEERING_LEVERS", 10),
      ("ORC_1", 2),
      ("BCM_1", 1),
      ("TRACTION_BUTTON", 1),
      ("ESP_8", 50),
      ("AUTO_PARK_REQUEST", 50),
      ("WHEEL_BUTTONS", 1),
      ("ACCEL_GAS_22F", 50),
      ("ACCEL_RELATED_120", 50),
      ("ACC_ERROR", 0),
      #("INERTIAL_SENSOR", 50),
      ("ESC_ACC_COPY", 50),
      ("ENGINE_RPM_HEV", 50),
      ("ECM_TRQ", 50),
      ("TCM_A7", 50),  
    ]

    if CP.enablehybridEcu:
      messages += [
        ("HYBRID_ECU", 1),
        ("AXLE_TORQ", 100),
        ("HEV_HMI", 10),
      ]

    if CP.enableBsm:
      messages.append(("BLIND_SPOT_WARNINGS", 2))


    if CP.carFingerprint in HYBRID_CARS:
      messages += CarState.get_hybrid_messages() 

    if CP.carFingerprint in RAM_CARS:
      messages += [
        ("EPS_3", 50),
        ("Transmission_Status", 50),
      ]
    else:
      messages += [
        ("GEAR", 50),
      ]
      messages += CarState.get_cruise_messages()    

    return CANParser(DBC[CP.carFingerprint]["pt"], messages, 0)

  @staticmethod
  def get_cam_can_parser(CP):

    messages = [
      ("LKAS_COMMAND", 100),
      ("LKAS_HEARTBIT", 10),
      ("LKAS_HUD", 4),
    ]

    if CP.carFingerprint in RAM_CARS:
      messages += CarState.get_cruise_messages()
    #else:
      # LKAS_HEARTBIT data needs to be forwarded!
    #  forward_lkas_heartbit_messages = [
    #    ("LKAS_HEARTBIT", 10),
    #  ]
    #  messages += forward_lkas_heartbit_messages
    
    return CANParser(DBC[CP.carFingerprint]["pt"], messages, 2)
