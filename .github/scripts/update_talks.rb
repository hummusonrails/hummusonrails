require 'httparty'
require 'octokit'
require 'base64'
require 'json'

# Fetch talks from the API
url = "https://www.bengreenberg.dev/api/talks.json"
response = HTTParty.get(url)
talks = JSON.parse(response.body)

# Generate the updated talks list (top 5)
talks_list = ["Some recent talks I've given at conferences include:\n"]
talks.first(5).each do |talk|
  title = talk['presentation']
  conference = talk['conference']
  link = talk['link']
  talks_list << "* [#{title}](#{link}) at #{conference}"
end
talks_list << "\n"

# Update the README.md file
client = Octokit::Client.new(access_token: ENV['GITHUB_TOKEN'])
repo = ENV['GITHUB_REPOSITORY']
readme = client.readme(repo)
readme_content = Base64.decode64(readme[:content]).force_encoding('UTF-8')

# Remove the existing talks list if present
talks_regex = /Some recent talks I've given at conferences include:[\s\S]*?(?=I've also had a chance to chat with some great people on podcasts including:)/m
updated_content = readme_content.sub(talks_regex, talks_list.join("\n"))

client.update_contents(repo, 'README.md', 'Update recent talks', readme[:sha], updated_content)
