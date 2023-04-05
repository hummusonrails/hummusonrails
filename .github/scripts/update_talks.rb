require 'httparty'
require 'nokogiri'
require 'octokit'

# Scrape talks from the website
url = "https://www.bengreenberg.dev/talks/"
response = HTTParty.get(url)
parsed_page = Nokogiri::HTML(response.body)
talks = parsed_page.css('tbody tr')

# Generate the updated talks list (top 5)
talks_list = ["\nSome recent talks I've given at conferences include:\n"]
talks.first(5).each do |talk|
  presentation = talk.css('div.text-sm.font-medium.text-gray-900').text.strip
  conference = talk.css('td:nth-child(2) div.text-sm.text-gray-900').text.strip
  watch_link = talk.at_css('a')[:href]
  talks_list << "* [#{presentation}](#{watch_link}) at #{conference}"
end

# Update the README.md file
client = Octokit::Client.new(access_token: ENV['GITHUB_TOKEN'])
repo = ENV['GITHUB_REPOSITORY']
readme = client.readme(repo)
readme_content = Base64.decode64(readme[:content]).force_encoding('UTF-8')

# Remove the existing talks list if present, otherwise, replace the placeholder
talks_regex = /(?<=\nSome recent talks I've given at conferences include:\n)([\s\S]*?)(?=\n\n)/m
updated_content = readme_content.match(talks_regex) ? readme_content.sub(talks_regex, talks_list[1..].join("\n")) : readme_content.sub("<!-- recent_talks_placeholder -->", talks_list.join("\n"))

client.update_contents(repo, 'README.md', 'Update recent talks', readme[:sha], updated_content)
