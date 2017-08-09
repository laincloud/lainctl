import re

def regex_match(patten, input):
    regex = re.compile(patten)
    match = regex.match(input)
    return match.groups()