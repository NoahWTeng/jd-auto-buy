#!/usr/bin/env python
# -*- coding:utf-8 -*-

from jd_auto_buy import JDWrapper
import sys

a = """
功能列表：                                                                                
 1.预约商品
 2.秒杀抢购商品
"""

if __name__ == '__main__':
    print(a)
    JDHelper = JDWrapper()  # 初始化
    choice_function = input('请选择:')
    if choice_function == '1':
        JDHelper.reserve()
    elif choice_function == '2':
        JDHelper.pull_off_proc_pool ()
    else:
        print('没有此功能')
        sys.exit(1)
