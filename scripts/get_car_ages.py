#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Tue Apr  2 14:18:44 2024

@author: lisa
"""

import tabula
import numpy as np

fn = "/home/lisa/Documents/paper/transport/ACEA-Report-Vehicles-on-European-roads-.pdf"

def distirbute_cars(row):   
    if row.name=="EUROPEAN UNION": return row
    i = 0
    for j, number in enumerate(row): 
        if np.isnan(number):
            i += 1
        else:
            distributed = number / (i+1)
            row.iloc[j-i:j+1] = distributed
            i = 0
    return row

def read_ages_from_pdf(fn, page, name="CARS BY AGE"):
    tables = tabula.read_pdf(fn, pages=page)
    table = tables[0]
    table.set_index(name, inplace=True)

    table.columns = table.iloc[1,:]
    table = table.iloc[2:,:]

    table = table.applymap(lambda x: np.nan if isinstance(x, str) and '–' in x else x)
    table = table.apply(lambda x: (x.str.replace(",","")).astype(float))

    table = table.apply(lambda x: distirbute_cars(x), axis=1)
    
    return table
# car ages
car_ages = read_ages_from_pdf(fn, 11, "CARS BY AGE")

# trucks ages
truck_ages = read_ages_from_pdf(fn, 13, "TRUCKS BY AGE")
