# -*- coding: utf-8 -*-

import json
import os
import pickle
import random
from time import time, sleep
from lxml import etree

import requests

from config import global_config
from exception import AsstException
from log import logger
from messenger import Messenger
from timer import Timer
from utils import get_random_user_agent
from utils import response_status, save_image, open_image, parse_json, check_login, wait_some_time
from variables import DEFAULT_USER_AGENT
from concurrent.futures import ProcessPoolExecutor


class JDSession:
    """
    ===================================
      COOKIES
    ===================================
    """
    use_random_ua = global_config.getboolean('config', 'random_user_agent')

    def __init__(self):
        self.user_agent = DEFAULT_USER_AGENT if not self.use_random_ua else get_random_user_agent()
        self.sess = self.__start_session()

    def __start_session(self):
        session = requests.session()
        session.headers = self.get_headers()
        return session

    def get_headers(self):
        return {"User-Agent": self.user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;"
                          "q=0.9,image/webp,image/apng,*/*;"
                          "q=0.8,application/signed-exchange;"
                          "v=b3",
                "Connection": "keep-alive"}

    def get_user_agent(self):
        return self.user_agent

    def get_session(self):
        return self.sess

    def get_cookies(self):
        return self.get_session().cookies

    def set_cookies(self, cookies):
        self.sess.cookies.update(cookies)

    def validate_cookies(self):
        """验证cookies是否有效（是否登陆）
        通过访问用户订单列表页进行判断：若未登录，将会重定向到登陆页面。
        :return: cookies是否有效 True/False
        """
        url = 'https://order.jd.com/center/list.action'
        payload = {
            'rid': str(int(time() * 1000)),
        }
        try:
            resp = self.sess.get(url=url, params=payload, allow_redirects=False)
            if resp.status_code == requests.codes.OK:
                return True
        except Exception as e:
            logger.error(e)
        return False

    def load_cookies(self):
        cookies_file = ''
        for name in os.listdir('./cookies'):
            if name.endswith('.cookies'):
                cookies_file = './cookies/{0}'.format(name)
                break

        if cookies_file == '':
            return False

        with open(cookies_file, 'rb') as f:
            local_cookies = pickle.load(f)
        self.set_cookies(local_cookies)

    def save_cookies(self, nick_name):
        cookies_file = './cookies/{0}.cookies'.format(nick_name)
        directory = os.path.dirname(cookies_file)
        if not os.path.exists(directory):
            os.makedirs(directory)
        with open(cookies_file, 'wb') as f:
            pickle.dump(self.sess.cookies, f)


class JDLogin:
    """
    ===================================
      LOGIN
    ===================================
    """

    def __init__(self, jd_session: JDSession):
        self.qr_code_file = 'qr_code.png'
        self.jd_session = jd_session
        self.sess = self.jd_session.get_session()
        self.is_login = False
        self.login_status_checker()

    def login_status_checker(self):
        self.is_login = self.jd_session.validate_cookies()

    def get_login_page(self):
        url = "https://passport.jd.com/new/login.aspx"
        page = self.sess.get(url, headers=self.jd_session.get_headers())
        return page

    def get_qrcode(self):
        url = 'https://qr.m.jd.com/show'
        payload = {
            'appid': 133,
            'size': 250,
            't': str(int(time() * 1000)),
        }
        headers = {
            'User-Agent': self.jd_session.get_user_agent(),
            'Referer': 'https://passport.jd.com/new/login.aspx',
        }
        resp = self.sess.get(url=url, headers=headers, params=payload)

        if not response_status(resp):
            logger.info('获取二维码失败')
            return False

        save_image(resp, self.qr_code_file)
        logger.info('请打开京东手机客户端，准备扫码登陆:')
        open_image(self.qr_code_file)
        return True

    def get_qrcode_ticket(self):
        url = 'https://qr.m.jd.com/check'
        payload = {
            'appid': '133',
            'callback': 'jQuery{}'.format(random.randint(1000000, 9999999)),
            'token': self.sess.cookies.get('wlfstk_smdl'),
            '_': str(int(time() * 1000)),
        }
        headers = {
            'User-Agent': self.jd_session.get_user_agent(),
            'Referer': 'https://passport.jd.com/new/login.aspx',
        }
        resp = self.sess.get(url=url, headers=headers, params=payload)

        if not response_status(resp):
            logger.error('获取二维码扫描结果异常')
            return False

        resp_json = parse_json(resp.text)

        if resp_json['code'] != 200:
            logger.info('Code: %s, Message: %s', resp_json['code'], resp_json['msg'])
            return None
        else:
            logger.info('已完成手机客户端确认')
            return resp_json['ticket']

    def validate_qrcode_ticket(self, ticket):
        url = 'https://passport.jd.com/uc/qrCodeTicketValidation'
        headers = {
            'User-Agent': self.jd_session.get_user_agent(),
            'Referer': 'https://passport.jd.com/uc/login?ltype=logout',
        }
        resp = self.sess.get(url=url, headers=headers, params={'t': ticket})
        if not response_status(resp):
            return False

        resp_json = json.loads(resp.text)
        if resp_json['returnCode'] == 0:
            return True
        else:
            logger.info(resp_json)
            return False

    def login_by_qrcode(self):
        self.get_login_page()

        # download QR code
        if not self.get_qrcode():
            raise AsstException('二维码下载失败')

        # get QR code ticket
        retry_times = 85
        for _ in range(retry_times):
            ticket = self.get_qrcode_ticket()
            logger.info(f'Ticket: {ticket}')
            if ticket:
                break
            sleep(2)
        else:
            raise AsstException('二维码过期，请重新获取扫描')

        # validate QR code ticket
        if not self.validate_qrcode_ticket(ticket):
            raise AsstException('二维码信息校验失败')

        self.login_status_checker()

        logger.info('二维码登录成功')

    def get_user_info(self):
        url = 'https://passport.jd.com/user/petName/getUserInfoForMiniJd.action'
        payload = {
            'callback': 'jQuery{}'.format(random.randint(1000000, 9999999)),
            '_': str(int(time() * 1000)),
        }
        headers = {
            'User-Agent': self.jd_session.get_user_agent(),
            'Referer': 'https://order.jd.com/center/list.action',
        }

        resp = self.sess.get(url=url, params=payload, headers=headers)
        resp_json = parse_json(resp.text)
        logger.info(resp_json)
        return resp_json.get('nickName') or 'jd'


class JDWrapper(object):
    def __init__(self):
        self.uuid = global_config.get('config', 'uuid')
        self.eid = global_config.get('config', 'eid')
        self.fp = global_config.get('config', 'fp')
        self.sku_id = global_config.get('product', 'sku_id')
        self.quantity = global_config.get('product', 'quantity')
        self.send_message = global_config.getboolean('messenger', 'enable')
        self.messenger = Messenger(global_config.get('messenger', 'sckey')) if self.send_message else None

        self.jd_session = JDSession()
        self.jd_session.load_cookies()

        self.qr_login = JDLogin(self.jd_session)
        self.is_login = self.qr_login.is_login
        self.session = self.jd_session.get_session()
        self.user_agent = self.jd_session.user_agent
        self.nick_name = None

        self.timer = Timer()

        self.process_pool = global_config.get('config', 'process_pool')

        self.pull_off_url = dict()
        self.pull_off_init_info = dict()
        self.order_data = dict()

    def login_by_qrcode(self):
        if self.qr_login.is_login:
            logger.info('登录成功')
            return

        self.qr_login.login_by_qrcode()

        if self.qr_login.is_login:
            self.nick_name = self.qr_login.get_user_info()
            self.jd_session.save_cookies(self.nick_name)
        else:
            raise AsstException("二维码登录失败！")

    """
    ===================================
    RESERVE
    ===================================
    """

    @check_login
    def reserve(self):
        while True:
            try:
                self.make_reserve()
                break
            except Exception as e:
                logger.info('预约发生异常!', e)
            wait_some_time()

    def get_sku_title(self):
        """获取商品名称"""
        url = 'https://item.jd.com/{}.html'.format(global_config.get('product', 'sku_id'))
        resp = self.session.get(url).content
        x_data = etree.HTML(resp)
        sku_title = x_data.xpath('/html/head/title/text()')
        return sku_title[0]

    def make_reserve(self):
        """商品预约"""
        logger.info('商品名称:{}'.format(self.get_sku_title()))
        url = 'https://yushou.jd.com/youshouinfo.action?'
        payload = {
            'callback': 'fetchJSON',
            'sku': self.sku_id,
            '_': str(int(time() * 1000)),
        }
        headers = {
            'User-Agent': self.user_agent,
            'Referer': 'https://item.jd.com/{}.html'.format(self.sku_id),
        }
        resp = self.session.get(url=url, params=payload, headers=headers)
        resp_json = parse_json(resp.text)
        reserve_url = resp_json.get('url')
        # self.timer.start()
        while True:
            try:
                self.session.get(url='https:' + reserve_url)
                logger.info('预约成功，已获得抢购资格 / 您已成功预约过了，无需重复预约')
                if global_config.getboolean('messenger', 'enable') == 'true':
                    success_message = "预约成功，已获得抢购资格 / 您已成功预约过了，无需重复预约"
                    self.send_message(success_message)
                break
            except Exception as e:
                logger.error('预约失败正在重试...')

    """
    ===================================
    PULL OFF
    ===================================
    """

    @check_login
    def pull_off_proc_pool(self):
        self.nick_name = self.qr_login.get_user_info()
        with ProcessPoolExecutor(int(self.process_pool)) as pool:
            for i in range(int(self.process_pool)):
                pool.submit(self.pull_off)

    @check_login
    def pull_off(self):
        while True:
            try:
                self.request_url()
                while True:
                    self.request_checkout_page()
                    self.submit_order()
            except Exception as e:
                logger.info('抢购发生异常，稍后继续执行！', e)
            wait_some_time()

    def request_url(self):
        """访问商品的抢购链接（用于设置cookie等"""
        logger.info('用户:{}'.format(self.nick_name))
        logger.info('商品名称:{}'.format(self.get_sku_title()))
        self.timer.start()
        self.pull_off_url[self.sku_id] = self.get_url()
        logger.info('访问商品的抢购连接...')
        headers = {
            'User-Agent': self.user_agent,
            'Host': 'marathon.jd.com',
            'Referer': 'https://item.jd.com/{}.html'.format(self.sku_id),
        }
        self.session.get(
            url=self.pull_off_url.get(
                self.sku_id),
            headers=headers,
            allow_redirects=False)

    def get_url(self):
        url = 'https://itemko.jd.com/itemShowBtn'
        payload = {
            'callback': 'jQuery{}'.format(random.randint(1000000, 9999999)),
            'skuId': self.sku_id,
            'from': 'pc',
            '_': str(int(time() * 1000)),
        }
        headers = {
            'User-Agent': self.user_agent,
            'Host': 'itemko.jd.com',
            'Referer': 'https://item.jd.com/{}.html'.format(self.sku_id),
        }
        while True:
            resp = self.session.get(url=url, headers=headers, params=payload)
            resp_json = parse_json(resp.text)
            if resp_json.get('url'):
                # https://divide.jd.com/user_routing?skuId=8654289&sn=c3f4ececd8461f0e4d7267e96a91e0e0&from=pc
                router_url = 'https:' + resp_json.get('url')
                # https://marathon.jd.com/captcha.html?skuId=8654289&sn=c3f4ececd8461f0e4d7267e96a91e0e0&from=pc
                pull_off_url = router_url.replace(
                    'divide', 'marathon').replace(
                    'user_routing', 'captcha.html')
                logger.info("抢购链接获取成功: %s", pull_off_url)
                return pull_off_url
            else:
                logger.info("抢购链接获取失败，稍后自动重试")
                wait_some_time()

    def request_checkout_page(self):
        """访问抢购订单结算页面"""
        logger.info('访问抢购订单结算页面...')
        url = 'https://marathon.jd.com/seckill/seckill.action'
        payload = {
            'skuId': self.sku_id,
            'num': self.quantity,
            'rid': int(time())
        }
        headers = {
            'User-Agent': self.user_agent,
            'Host': 'marathon.jd.com',
            'Referer': 'https://item.jd.com/{}.html'.format(self.sku_id),
        }
        self.session.get(url=url, params=payload, headers=headers, allow_redirects=False)

    def get_init_info(self):
        logger.info('获取秒杀初始化信息...')
        url = 'https://marathon.jd.com/seckillnew/orderService/pc/init.action'
        data = {
            'sku': self.sku_id,
            'num': self.quantity,
            'isModifyAddress': 'false',
        }
        headers = {
            'User-Agent': self.user_agent,
            'Host': 'marathon.jd.com',
        }
        resp = self.session.post(url=url, data=data, headers=headers)

        resp_json = None
        try:
            resp_json = parse_json(resp.text)
        except Exception:
            raise AsstException('抢购失败，返回信息:{}'.format(resp.text[0: 128]))

        return resp_json

    def get_order_data(self):
        logger.info('生成提交抢购订单所需参数...')
        # 获取用户秒杀初始化信息
        self.pull_off_init_info[self.sku_id] = self.get_init_info()
        init_info = self.pull_off_init_info.get(self.sku_id)
        default_address = init_info['addressList'][0]  # 默认地址dict
        invoice_info = init_info.get('invoiceInfo', {})  # 默认发票信息dict, 有可能不返回
        token = init_info['token']
        data = {
            'skuId': self.sku_id,
            'num': self.quantity,
            'addressId': default_address['id'],
            'yuShou': 'true',
            'isModifyAddress': 'false',
            'name': default_address['name'],
            'provinceId': default_address['provinceId'],
            'cityId': default_address['cityId'],
            'countyId': default_address['countyId'],
            'townId': default_address['townId'],
            'addressDetail': default_address['addressDetail'],
            'mobile': default_address['mobile'],
            'mobileKey': default_address['mobileKey'],
            'email': default_address.get('email', ''),
            'postCode': '',
            'invoiceTitle': invoice_info.get('invoiceTitle', -1),
            'invoiceCompanyName': '',
            'invoiceContent': invoice_info.get('invoiceContentType', 1),
            'invoiceTaxpayerNO': '',
            'invoiceEmail': '',
            'invoicePhone': invoice_info.get('invoicePhone', ''),
            'invoicePhoneKey': invoice_info.get('invoicePhoneKey', ''),
            'invoice': 'true' if invoice_info else 'false',
            'password': global_config.get('account', 'payment_pwd'),
            'codTimeType': 3,
            'paymentType': 4,
            'areaCode': '',
            'overseas': 0,
            'phone': '',
            'eid': global_config.get('config', 'eid'),
            'fp': global_config.get('config', 'fp'),
            'token': token,
            'pru': ''
        }
        return data

    def submit_order(self):
        url = 'https://marathon.jd.com/seckillnew/orderService/pc/submitOrder.action'
        payload = {
            'skuId': self.sku_id,
        }
        try:
            self.order_data[self.sku_id] = self.get_order_data()
        except Exception as e:
            logger.info('抢购失败，无法获取生成订单的基本信息，接口返回:【{}】'.format(str(e)))
            return False

        logger.info('提交抢购订单...')
        headers = {
            'User-Agent': self.user_agent,
            'Host': 'marathon.jd.com',
            'Referer': 'https://marathon.jd.com/seckill/seckill.action?skuId={0}&num={1}&rid={2}'.format(
                self.sku_id, self.quantity, int(time())),
        }
        resp = self.session.post(
            url=url,
            params=payload,
            data=self.order_data.get(
                self.sku_id),
            headers=headers)
        resp_json = None
        try:
            resp_json = parse_json(resp.text)
        except Exception as e:
            logger.info('抢购失败，返回信息:{}'.format(resp.text[0: 128]))
            return False
        # 返回信息
        # 抢购失败：
        # {'errorMessage': '很遗憾没有抢到，再接再厉哦。', 'orderId': 0, 'resultCode': 60074, 'skuId': 0, 'success': False}
        # {'errorMessage': '抱歉，您提交过快，请稍后再提交订单！', 'orderId': 0, 'resultCode': 60017, 'skuId': 0, 'success': False}
        # {'errorMessage': '系统正在开小差，请重试~~', 'orderId': 0, 'resultCode': 90013, 'skuId': 0, 'success': False}
        # 抢购成功：
        # {"appUrl":"xxxxx","orderId":820227xxxxx,"pcUrl":"xxxxx","resultCode":0,"skuId":0,"success":true,"totalMoney":"xxxxx"}
        if resp_json.get('success'):
            order_id = resp_json.get('orderId')
            total_money = resp_json.get('totalMoney')
            pay_url = 'https:' + resp_json.get('pcUrl')
            logger.info('抢购成功，订单号:{}, 总价:{}, 电脑端付款链接:{}'.format(order_id, total_money, pay_url))
            if global_config.getboolean('messenger', 'enable') == 'true':
                success_message = "抢购成功，订单号:{}, 总价:{}, 电脑端付款链接:{}".format(order_id, total_money, pay_url)
                self.send_message(success_message)
            return True
        else:
            logger.info('抢购失败，返回信息:{}'.format(resp_json))
            if global_config.getboolean('messenger', 'enable') == 'true':
                error_message = '抢购失败，返回信息:{}'.format(resp_json)
                self.send_message(error_message)
            return False



# class JDCart:
#
#     def __init__(self):
# """
# ===================================
# CART
# ===================================
# """
#
# @check_login
# def clear_cart(self):
#     """清空购物车
#
#              包括两个请求：
#              1.选中购物车中所有的商品
#              2.批量删除
#
#              :return: 清空购物车结果 True/False
#              """
#     # select_url = 'https://cart.jd.com/selectAllItem.action'
#     # remove_url_many = 'https://cart.jd.com/batchRemoveSkusFromCart.action'
#     # data = {
#     #     't': 0,
#     #     'outSkus': '',
#     #     'random': random.random(),
#     # }
#     cart_data = self.get_cart_detail()
#     remove_url_one = 'https://cart.jd.com/removeSkuFromCart.action'
#
#     body = {
#         "pid": "",
#         "ptype": "1",
#         "t": "0",
#         "outSkus": "",
#         'random': random.random(),
#     }
#     if bool(cart_data):
#         for name, value in cart_data['vendors'][0]['sorted'][0]['item'].items():
#             if name == 'Id':
#                 body['pid'] = value
#     else:
#         logger.info('Cart is empty')
#         return
#     try:
#         self.sess.post(url=remove_url_one, data=body)
#         # select_resp = self.sess.post(url=select_url, data=data)
#         # remove_resp = self.sess.post(url=remove_url_many, data=data)
#         # if (not response_status(select_resp)) or (not response_status(remove_resp)):
#         #     logger.error('购物车清空失败')
#         #     return False
#         logger.info('购物车清空成功')
#         return True
#     except Exception as e:
#         logger.error(e)
#         return False

# """
# ===================================
# ADD ITEM
# ===================================
# """
#
# @check_login
# def get_cart_detail(self):
#     """获取购物车商品详情
#     :return: 购物车商品信息 dict
#     """
#     url_post = 'https://api.m.jd.com/api'
#     headers = {
#         'User-Agent': self.user_agent,
#         'Referer': 'https://cart.jd.com/',
#     }
#     params = {
#         'functionId': 'pcCart_jc_getCurrentCart',
#         'appid': 'JDC_mall_cart',
#     }
#
#     cookies = self.sess.cookies.get_dict()
#     resp = self.sess.post(url=url_post, params=params, headers=headers, cookies=cookies)
#     logger.info(resp.json())
#     result = resp.json()['resultData']['cartInfo']
#     return result
#
# def _get_item_detail_page(self, sku_ids):
#     item_info = {
#         'id': sku_ids,
#         'name': '',
#         'price': '',
#     }
#
#     try:
#         item_link = 'http://item.jd.com/{0}.html'.format(sku_ids)
#         resp = self.sess.get(item_link)
#
#         soup = BeautifulSoup(resp.text, "html.parser")
#         tags = soup.select_one('div.sku-name')
#         item_info['name'] = tags.text.strip(' \t\r\n')
#         item_info['price'] = self._get_item_price(sku_ids)
#         return item_info
#     except Exception as e:
#         logger.error(e)
#
# def _get_item_price(self, sku_id):
#     """获取商品价格
#     :param sku_id: 商品id
#     :return: 价格
#     """
#     url = 'http://p.3.cn/prices/mgets'
#     payload = {
#         'type': 1,
#         'pduid': int(time() * 1000),
#         'skuIds': 'J_' + sku_id,
#     }
#     resp = self.sess.get(url=url, params=payload)
#     return parse_json(resp.text).get('p')
#
# @check_login
# def add_item_to_cart(self, sku_ids):
#     """添加商品到购物车
#     重要：
#     1.商品添加到购物车后将会自动被勾选✓中。
#     2.在提交订单时会对勾选的商品进行结算。
#     3.部分商品（如预售、下架等）无法添加到购物车
#     京东购物车可容纳的最大商品种数约为118-120种，超过数量会加入购物车失败。
#     :param sku_ids: 商品id，格式："123" 或 "123,456" 或 "123:1,456:2"。若不配置数量，默认为1个。
#     :return:
#     """
#     url = 'https://cart.jd.com/gate.action'
#     headers = {
#         'User-Agent': self.user_agent,
#     }
#     try:
#         for sku_id, count in parse_sku_id(sku_ids=sku_ids).items():
#             payload = {
#                 'pid': sku_id,
#                 'pcount': count,
#                 'ptype': 1,
#             }
#             resp = self.sess.get(url=url, headers=headers, params=payload)
#             logger.info(resp.url)
#
#             result = bool(self.get_cart_detail())
#             if result:
#                 logger.info('%s x %s 已成功加入购物车', sku_id, count)
#                 return True
#             else:
#                 logger.error('%s 添加到购物车失败', sku_id)
#                 return False
#     except Exception as e:
#         logger.error('Error add to cart：%s', e)
#
# @check_login
# def get_checkout_page_detail(self):
#     """获取订单结算页面信息
#
#     该方法会返回订单结算页面的详细信息：商品名称、价格、数量、库存状态等。
#
#     :return: 结算信息 dict
#     """
#     data = {
#         't': 0,
#         'outSkus': '',
#         'random': random.random(),
#     }
#
#     select_url = 'https://cart.jd.com/selectAllItem.action'
#
#     url = 'http://trade.jd.com/shopping/order/getOrderInfo.action'
#     # url = 'https://cart.jd.com/gotoOrder.action'
#     payload = {
#         'rid': str(int(time() * 1000)),
#     }
#     try:
#         self.sess.post(url=select_url, data=data)
#         resp = self.sess.get(url=url, params=payload)
#         if not response_status(resp):
#             logger.error('获取订单结算页信息失败')
#             return
#         soup = BeautifulSoup(resp.text, "html.parser")
#         order_detail = {
#             'address': soup.find('span', id='sendAddr').text[5:],  # remove '寄送至： ' from the begin
#             'receiver': soup.find('span', id='sendMobile').text[4:],  # remove '收件人:' from the begin
#             'total_price': soup.find('span', id='sumPayPriceId').text[1:],  # remove '￥' from the begin
#             'items': []
#         }
#         logger.info("下单信息：%s", order_detail)
#         return True
#     except Exception as e:
#         logger.error('订单结算页面数据解析异常（可以忽略），报错信息：%s', e)
#         return False
#
# @check_login
# def submit_order(self):
#     """提交订单
#
#           重要：
#           1.该方法只适用于普通商品的提交订单（即可以加入购物车，然后结算提交订单的商品）
#           2.提交订单时，会对购物车中勾选✓的商品进行结算（如果勾选了多个商品，将会提交成一个订单）
#
#           :return: True/False 订单提交结果
#           """
#     url = 'https://trade.jd.com/shopping/order/submitOrder.action'
#     # js function of submit order is included in https://trade.jd.com/shopping/misc/js/order.js?r=2018070403091
#     logger.info(f'trackId: {self.trackId}')
#     data = {
#         'overseaPurchaseCookies': '',
#         'submitOrderParam.btSupport': '1',
#         'submitOrderParam.ignorePriceChange': '0',
#         'submitOrderParam.sopNotPutInvoice': 'false',
#         'submitOrderParam.trackID': self.trackId,
#         'submitOrderParam.eid': self.eid,
#         'submitOrderParam.fp': self.fp,
#     }
#
#     # add payment password when necessary
#     payment_pwd = global_config.get('account', 'payment_pwd')
#     if payment_pwd:
#         data['submitOrderParam.payPassword'] = encrypt_payment_pwd(payment_pwd)
#
#     headers = {
#         'User-Agent': self.user_agent,
#         'Host': 'trade.jd.com',
#         'Referer': 'http://trade.jd.com/shopping/order/getOrderInfo.action',
#     }
#     try:
#         resp = self.sess.post(url=url, data=data, headers=headers)
#         resp_json = json.loads(resp.text)
#         logger.info(resp_json)
#
#         if resp_json.get('success'):
#             order_id = resp_json.get('orderId')
#             logger.info('订单提交成功! 订单号：%s', order_id)
#             if self.send_message:
#                 self.messenger.send(text='JD 订单提交成功', desp='订单号：%s' % order_id)
#             return True
#         else:
#             logger.info('订单提交失败')
#             return False
#     except Exception as e:
#         logger.error(e)
#         return False
#
# @check_login
# def submit_order_by(self, buy_time, retry=4, interval=5, sku_id='', type_time=False):
#
#     if type_time:
#         t = Timer(buy_time=buy_time)
#         t.start()
#         for count in range(1, retry + 1):
#             logger.info('第[%s/%s]次尝试提交订单', count, retry)
#             self.clear_cart()
#             self.add_item_to_cart(sku_id)
#             if self.get_checkout_page_detail():
#                 self.submit_order()
#                 break
#             else:
#                 self.clear_cart()
#                 logger.info('Cant checkout %ss', interval)
#                 sleep(interval)
#         else:
#             logger.info('执行结束，提交订单失败！')
#     else:
#         self.clear_cart()
#         self.add_item_to_cart(sku_id)
#         if bool(self.get_cart_detail()):
#             self.get_checkout_page_detail()
#             self.submit_order()
