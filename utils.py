
def extract_tool_from_channel(channel):
    if channel and hasattr(channel, "name") and channel.name.startswith("signout-"):
        return channel.name.replace("signout-", "")
    return None
